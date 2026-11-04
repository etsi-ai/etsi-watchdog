# etsi/watchdog/streaming/stats/base.py

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from etsi.watchdog.drift.base import DriftResult


class StreamingStat(ABC):
    """
    Abstract base class for streaming incremental statistics.
    All implementations must support incremental updates, serialization,
    state restoration, and merging across window slices (for hopping windows).
    """
    algo: str
    n: int

    @abstractmethod
    def update(self, x: float) -> None:
        """Incrementally update the statistic with a single float value."""
        pass

    @abstractmethod
    def merge(self, other: "StreamingStat", rng_seed: int = 0) -> "StreamingStat":
        """Merge another compatible stat object into a new StreamingStat instance."""
        pass

    @abstractmethod
    def finalize(
        self,
        ref: Any,
        threshold: float,
        meta: Optional[Dict[str, Any]] = None
    ) -> DriftResult:
        """Compute the final DriftResult against the reference distribution."""
        pass

    @abstractmethod
    def to_state(self) -> Dict[str, Any]:
        """
        Serialize state into msgpack-safe primitives (int, float, str, list).
        All integers must be < 2**64.
        """
        pass

    @classmethod
    @abstractmethod
    def from_state(cls, d: Dict[str, Any]) -> "StreamingStat":
        """Reconstruct a stat instance from serialized state dictionary."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset the internal counts/state to empty."""
        pass
