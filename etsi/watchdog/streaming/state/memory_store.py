# etsi/watchdog/streaming/state/memory_store.py

import time
import threading
from typing import Optional, Dict, List, Sequence, Callable, Any
from .base import StateStore


class InMemoryStateStore(StateStore):
    """
    Thread-safe in-memory state store for Milestone 1 and broker-free unit tests.
    Supports atomic write_batch, TTL expiry with injectable clock, and snapshot/restore.
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._lock = threading.RLock()
        self._data: Dict[str, bytes] = {}
        self._expiry: Dict[str, float] = {}
        self._clock: Callable[[], float] = clock or time.time
        self.owner_token: Optional[str] = None

    def _is_expired(self, key: str, now: float) -> bool:
        if key in self._expiry and self._expiry[key] <= now:
            return True
        return False

    def _cleanup_expired(self, key: str, now: float) -> None:
        if self._is_expired(key, now):
            self._data.pop(key, None)
            self._expiry.pop(key, None)

    def get(self, key: str) -> Optional[bytes]:
        with self._lock:
            now = self._clock()
            self._cleanup_expired(key, now)
            return self._data.get(key)

    def get_many(self, keys: Sequence[str]) -> Dict[str, bytes]:
        with self._lock:
            now = self._clock()
            result: Dict[str, bytes] = {}
            for k in keys:
                self._cleanup_expired(k, now)
                val = self._data.get(k)
                if val is not None:
                    result[k] = val
            return result

    def list_prefix(self, prefix: str) -> List[str]:
        with self._lock:
            now = self._clock()
            keys_to_purge = [k for k in self._data if k.startswith(prefix) and self._is_expired(k, now)]
            for k in keys_to_purge:
                self._data.pop(k, None)
                self._expiry.pop(k, None)

            return sorted([k for k in self._data if k.startswith(prefix)])

    def write_batch(
        self,
        puts: Dict[str, bytes],
        deletes: Sequence[str],
        ttl: Optional[Dict[str, int]] = None,
        fence: Optional[str] = None,
    ) -> None:
        """
        Atomically apply batch modifications under lock.
        Validates fence token if active owner token is configured.
        """
        with self._lock:
            if fence is not None and self.owner_token is not None and self.owner_token != fence:
                raise PermissionError(
                    f"Fencing token mismatch: active owner is '{self.owner_token}', provided fence is '{fence}'"
                )

            now = self._clock()

            # Apply puts and TTLs
            for k, val in puts.items():
                if not isinstance(val, (bytes, bytearray)):
                    raise TypeError(f"StateStore value for '{k}' must be bytes, got {type(val).__name__}")
                self._data[k] = bytes(val)
                if ttl and k in ttl and ttl[k] is not None:
                    self._expiry[k] = now + float(ttl[k])
                else:
                    self._expiry.pop(k, None)

            # Apply deletes
            for k in deletes:
                self._data.pop(k, None)
                self._expiry.pop(k, None)

    def snapshot(self) -> Dict[str, Any]:
        """Create a deep snapshot for test fixtures and crash recovery verification."""
        with self._lock:
            return {
                "data": dict(self._data),
                "expiry": dict(self._expiry),
                "owner_token": self.owner_token,
            }

    def restore(self, snap: Dict[str, Any]) -> None:
        """Restore state from snapshot."""
        with self._lock:
            self._data = dict(snap["data"])
            self._expiry = dict(snap["expiry"])
            self.owner_token = snap.get("owner_token")

    def close(self) -> None:
        pass
