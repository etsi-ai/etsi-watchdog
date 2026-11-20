# etsi/watchdog/streaming/state/lease.py

import time
import uuid
from typing import Optional, Any
from .base import LeaseAcquisitionError, LeaseLostError

# Lua script to renew lease only if token matches
RENEW_LUA = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
    return 1
else
    return 0
end
"""

# Lua script to release lease only if token matches
RELEASE_LUA = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
else
    return 0
end
"""


class RedisOwnerLease:
    """
    Manages single-instance distributed lease on Redis (D-5, PR-8).
    Guarantees only one consumer instance can write to state store at any time.
    """

    def __init__(
        self,
        redis_client: Any,
        owner_key: str,
        lease_ttl_seconds: float = 30.0,
        token: Optional[str] = None,
    ):
        self.client = redis_client
        self.owner_key: str = str(owner_key)
        self.lease_ttl_seconds: float = float(lease_ttl_seconds)
        self.ttl_ms: int = int(self.lease_ttl_seconds * 1000)
        self.token: str = token or str(uuid.uuid4())
        try:
            self._renew_script = self.client.register_script(RENEW_LUA)
            self._release_script = self.client.register_script(RELEASE_LUA)
        except Exception:
            self._renew_script = None
            self._release_script = None
        self._last_renewed_ts: float = 0.0

    def acquire(
        self,
        wait_timeout_seconds: Optional[float] = None,
        poll_interval_seconds: float = 0.5,
    ) -> bool:
        """
        Attempt to acquire lease using SET key token NX PX ttl_ms.
        Waits up to wait_timeout_seconds (defaulting to lease_ttl_seconds)
        for any previous owner's lease to naturally expire.
        """
        timeout = self.lease_ttl_seconds if wait_timeout_seconds is None else float(wait_timeout_seconds)
        start_time = time.monotonic()

        while True:
            # Try atomic set with NX and PX
            acquired = self.client.set(self.owner_key, self.token.encode("utf-8"), nx=True, px=self.ttl_ms)
            if acquired:
                self._last_renewed_ts = time.monotonic()
                return True

            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                current_owner = self.get_owner()
                raise LeaseAcquisitionError(
                    f"Could not acquire lease for key '{self.owner_key}' within {timeout:.1f}s. "
                    f"Current owner token: '{current_owner}'"
                )

            time.sleep(poll_interval_seconds)

    def renew_if_due(self) -> bool:
        """
        Renew lease if more than 1/3 of the TTL has elapsed since last renewal (D-5).
        """
        now = time.monotonic()
        if (now - self._last_renewed_ts) >= (self.lease_ttl_seconds / 3.0):
            return self.renew()
        return True

    def renew(self) -> bool:
        """
        Atomically renew lease TTL if this instance is still the owner.
        Raises LeaseLostError if the lease was lost or expired.
        """
        if self._renew_script is not None:
            try:
                res = self._renew_script(keys=[self.owner_key], args=[self.token.encode("utf-8"), self.ttl_ms])
                if res == 1:
                    self._last_renewed_ts = time.monotonic()
                    return True
            except Exception as e:
                if "unknown command" not in str(e).lower():
                    raise

        # Fallback to WATCH transaction
        pipe = self.client.pipeline(transaction=True)
        try:
            pipe.watch(self.owner_key)
            raw = pipe.get(self.owner_key)
            current = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if current == self.token:
                pipe.multi()
                pipe.pexpire(self.owner_key, self.ttl_ms)
                pipe.execute()
                self._last_renewed_ts = time.monotonic()
                return True
        finally:
            pipe.reset()

        current_owner = self.get_owner()
        raise LeaseLostError(
            f"Owner lease for key '{self.owner_key}' was lost. "
            f"Expected token '{self.token}', active owner is '{current_owner}'."
        )

    def release(self) -> bool:
        """
        Atomically release lease if still held by this instance.
        """
        if self._release_script is not None:
            try:
                res = self._release_script(keys=[self.owner_key], args=[self.token.encode("utf-8")])
                return bool(res == 1)
            except Exception as e:
                if "unknown command" not in str(e).lower():
                    raise

        # Fallback to WATCH transaction
        pipe = self.client.pipeline(transaction=True)
        try:
            pipe.watch(self.owner_key)
            raw = pipe.get(self.owner_key)
            current = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if current == self.token:
                pipe.multi()
                pipe.delete(self.owner_key)
                pipe.execute()
                return True
            pipe.unwatch()
            return False
        finally:
            pipe.reset()

    def is_held(self) -> bool:
        """Check if lease is currently held by this instance."""
        owner = self.get_owner()
        return owner == self.token

    def get_owner(self) -> Optional[str]:
        """Fetch current owner token from Redis."""
        raw = self.client.get(self.owner_key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)
