# etsi/watchdog/streaming/state/redis_store.py

from typing import Optional, Dict, List, Sequence, Any
import redis
from .base import StateStore, FenceMismatchError, StateStoreError

BATCH_WRITE_LUA = """
local owner_key = KEYS[1]
local fence = ARGV[1]
local current_owner = redis.call('GET', owner_key)

if current_owner ~= fence then
    return redis.error_reply("FENCE_MISMATCH: expected " .. fence .. ", got " .. tostring(current_owner))
end

local num_puts = tonumber(ARGV[2])
local idx = 3
for i = 1, num_puts do
    local k = ARGV[idx]
    local v = ARGV[idx + 1]
    local ttl = tonumber(ARGV[idx + 2])
    idx = idx + 3
    if ttl > 0 then
        redis.call('SETEX', k, ttl, v)
    else
        redis.call('SET', k, v)
    end
end

local num_dels = tonumber(ARGV[idx])
idx = idx + 1
for i = 1, num_dels do
    local k = ARGV[idx]
    idx = idx + 1
    redis.call('DEL', k)
end

return "OK"
"""


class RedisStateStore(StateStore):
    """
    Production Redis persistence backend for etsi-watchdog streaming (Task 4.2, PR-8).
    Supports atomic batch writes with Lua script fencing, Redis TTL expiry,
    and single-owner protection against zombie instances.
    """

    def __init__(
        self,
        redis_client: Optional[Any] = None,
        url: Optional[str] = None,
        owner_key: Optional[str] = None,
        **redis_kwargs: Any,
    ):
        if redis_client is not None:
            self._client = redis_client
        elif url is not None:
            self._client = redis.from_url(url, decode_responses=False, **redis_kwargs)
        else:
            self._client = redis.Redis(decode_responses=False, **redis_kwargs)

        self.owner_key: Optional[str] = owner_key
        try:
            self._batch_script = self._client.register_script(BATCH_WRITE_LUA)
        except Exception:
            self._batch_script = None

    def get(self, key: str) -> Optional[bytes]:
        """Retrieve binary value for a given key, or None if missing or expired."""
        val = self._client.get(key)
        if val is None:
            return None
        if isinstance(val, memoryview):
            return val.tobytes()
        return bytes(val)

    def get_many(self, keys: Sequence[str]) -> Dict[str, bytes]:
        """Batch retrieve multiple keys in one network round-trip."""
        if not keys:
            return {}
        key_list = list(keys)
        values = self._client.mget(key_list)
        result: Dict[str, bytes] = {}
        for k, v in zip(key_list, values):
            if v is not None:
                result[k] = v.tobytes() if isinstance(v, memoryview) else bytes(v)
        return result

    def list_prefix(self, prefix: str) -> List[str]:
        """List all active non-expired keys matching prefix*."""
        match_pattern = f"{prefix}*"
        keys: List[str] = []
        for k in self._client.scan_iter(match=match_pattern, count=100):
            if isinstance(k, bytes):
                keys.append(k.decode("utf-8", errors="replace"))
            else:
                keys.append(str(k))
        return sorted(keys)

    def write_batch(
        self,
        puts: Dict[str, bytes],
        deletes: Sequence[str],
        ttl: Optional[Dict[str, int]] = None,
        fence: Optional[str] = None,
    ) -> None:
        """
        Atomically apply batch puts and deletes.
        If fence is provided, validates that the active owner matches the fence token
        using Lua script fencing before any write takes effect.
        """
        ttl = ttl or {}

        # Validate types
        for k, v in puts.items():
            if not isinstance(v, (bytes, bytearray)):
                raise TypeError(f"StateStore value for '{k}' must be bytes, got {type(v).__name__}")

        if fence is not None:
            if not self.owner_key:
                raise ValueError("Cannot verify fence token without owner_key configured on RedisStateStore")

            if self._batch_script is not None:
                # Prepare Lua arguments:
                # ARGV: [fence, num_puts, (key, value, ttl)*, num_dels, (key)*]
                args: List[Any] = [fence.encode("utf-8"), len(puts)]
                for k, val in puts.items():
                    sec = int(ttl.get(k, 0))
                    sec = sec if sec > 0 else 0
                    args.extend([k, bytes(val), sec])

                del_list = list(deletes)
                args.append(len(del_list))
                args.extend(del_list)

                try:
                    self._batch_script(keys=[self.owner_key], args=args)
                    return
                except Exception as e:
                    err_msg = str(e)
                    if "FENCE_MISMATCH" in err_msg:
                        raise FenceMismatchError(
                            f"Atomic write_batch failed due to fence mismatch for owner key '{self.owner_key}': {err_msg}"
                        ) from e
                    if "unknown command" not in err_msg.lower():
                        raise StateStoreError(f"Failed to execute atomic write_batch: {err_msg}") from e

            # Fallback for environments where Lua/eval is disabled: WATCH-based atomic transaction
            pipe = self._client.pipeline(transaction=True)
            try:
                pipe.watch(self.owner_key)
                current = pipe.get(self.owner_key)
                current_token = current.decode("utf-8") if isinstance(current, bytes) else current
                if current_token != fence:
                    pipe.unwatch()
                    raise FenceMismatchError(
                        f"Atomic write_batch failed due to fence mismatch for owner key '{self.owner_key}' "
                        f"(expected '{fence}', active owner is '{current_token}')"
                    )

                pipe.multi()
                for k, val in puts.items():
                    sec = ttl.get(k)
                    if sec is not None and int(sec) > 0:
                        pipe.setex(k, int(sec), bytes(val))
                    else:
                        pipe.set(k, bytes(val))
                for k in deletes:
                    pipe.delete(k)
                pipe.execute()
            except FenceMismatchError:
                raise
            except Exception as e:
                raise StateStoreError(f"Failed to execute fallback write_batch: {e}") from e
            finally:
                pipe.reset()
        else:
            # Transactional pipeline execution without fence (MULTI / EXEC)
            try:
                pipe = self._client.pipeline(transaction=True)
                for k, val in puts.items():
                    sec = ttl.get(k)
                    if sec is not None and int(sec) > 0:
                        pipe.setex(k, int(sec), bytes(val))
                    else:
                        pipe.set(k, bytes(val))
                for k in deletes:
                    pipe.delete(k)
                pipe.execute()
            except Exception as e:
                raise StateStoreError(f"Failed to execute pipeline write_batch: {e}") from e

    def close(self) -> None:
        """Close connection to Redis."""
        try:
            self._client.close()
        except Exception:
            pass
