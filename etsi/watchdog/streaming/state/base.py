# etsi/watchdog/streaming/state/base.py

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Sequence


class StateStore(ABC):
    """
    Abstract Base Class for streaming state persistence backends (D-3).
    Guarantees interchangeable implementations across memory, Redis, and RocksDB.
    All write operations are executed atomically in write_batch.
    """

    @abstractmethod
    def get(self, key: str) -> Optional[bytes]:
        """Retrieve binary value for a given key, or None if missing or expired."""
        pass

    @abstractmethod
    def get_many(self, keys: Sequence[str]) -> Dict[str, bytes]:
        """Batch retrieve multiple keys in one operation."""
        pass

    @abstractmethod
    def list_prefix(self, prefix: str) -> List[str]:
        """List all active non-expired keys matching the given prefix."""
        pass

    @abstractmethod
    def write_batch(
        self,
        puts: Dict[str, bytes],
        deletes: Sequence[str],
        ttl: Optional[Dict[str, int]] = None,
        fence: Optional[str] = None,
    ) -> None:
        """
        Atomically apply puts, deletes, and optional per-key TTLs (in seconds).
        If fence is provided, the write must fail if the fence does not match the active owner.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Release backend connections and resources."""
        pass
