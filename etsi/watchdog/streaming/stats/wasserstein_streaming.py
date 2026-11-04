# etsi/watchdog/streaming/stats/wasserstein_streaming.py

from typing import Dict, Any, Optional
import numpy as np
from scipy.stats import wasserstein_distance
from etsi.watchdog.drift.base import DriftResult
from .base import StreamingStat
from .reference import ReferenceProfile
from .reservoir import ReservoirSample
from .rng import SplitMix64


class StreamingWassersteinStat(StreamingStat):
    """
    Streaming Wasserstein Distance (Earth Mover's Distance) via bounded reservoir sampling (Task 1.2, D-1, D-10).

    Approximation Error Characteristics:
    ------------------------------------
    The 1-Wasserstein metric between two one-dimensional distributions P and Q is defined as:
        W_1(P, Q) = int_0^1 |F_P^{-1}(t) - F_Q^{-1}(t)| dt
    measuring the minimum work required to transform one distribution into the other.

    Because the streaming current window is tracked using a bounded reservoir sample of capacity k
    (default 2,000, max 10,000), the empirical Wasserstein distance W_1(P_ref, Q_k) converges to the
    true distribution distance W_1(P_ref, Q) at the standard empirical measure rate of O(k^{-1/2})
    for distributions with finite second moments.

    Unlike scale-free statistics (e.g. KS or PSI), the Wasserstein distance is expressed in the raw physical
    units of the measured feature. Consequently, threshold selection is feature-specific and should be calibrated
    based on the domain-specific standard deviations or tolerances of each feature.
    """

    algo: str = "wasserstein"
    default_capacity: int = 2000
    default_threshold: float = 0.1

    def __init__(
        self,
        capacity: int = 2000,
        seed: Optional[int] = None,
        *args: Any,
        **kwargs: Any,
    ):
        # Guard against positional calls from legacy bucket-based signatures
        if capacity <= 100 and len(args) > 0 and isinstance(args[0], list):
            capacity = self.default_capacity

        self.capacity: int = int(capacity)
        self.reservoir: ReservoirSample = ReservoirSample(k=self.capacity, seed=seed)
        self.n: int = 0

    @classmethod
    def create_stat(
        cls,
        ref_prof: Optional[ReferenceProfile] = None,
        stream_id: str = "",
        feature: str = "",
        window_start: float = 0.0,
        capacity: int = 2000,
        base_seed: int = 0,
        *args: Any,
        **kwargs: Any,
    ) -> "StreamingWassersteinStat":
        """Factory method to construct an instance seeded deterministically from window identity."""
        seed = SplitMix64.seed(base_seed, stream_id, feature, window_start)
        return cls(capacity=capacity, seed=seed)

    def update(self, x: float) -> None:
        """Incrementally update reservoir sample with observation x."""
        self.reservoir.update(x)
        self.n = self.reservoir.n

    def merge(self, other: "StreamingStat", rng_seed: int = 0) -> "StreamingWassersteinStat":
        """Merge two StreamingWassersteinStat instances using hypergeometric reservoir sampling."""
        if not isinstance(other, StreamingWassersteinStat):
            raise TypeError(f"Cannot merge StreamingWassersteinStat with {type(other).__name__}")

        merged_res = self.reservoir.merge(other.reservoir, rng_seed=rng_seed)
        merged = StreamingWassersteinStat(capacity=self.capacity)
        merged.reservoir = merged_res
        merged.n = merged_res.n
        return merged

    def finalize(
        self,
        ref: ReferenceProfile,
        threshold: float = 0.1,
        meta: Optional[Dict[str, Any]] = None,
    ) -> DriftResult:
        """
        Evaluate Wasserstein distance between reference sample and window reservoir sample.
        Returns a DriftResult matching batch schema (score = wasserstein_distance).
        """
        if len(self.reservoir.values) == 0:
            dist = 0.0
        else:
            ref_vals = getattr(ref, "sorted_sample", None)
            if ref_vals is None or len(ref_vals) == 0:
                ref_vals = np.asarray(ref.ref_counts, dtype=np.float64)

            dist = float(wasserstein_distance(ref_vals, self.reservoir.values))

        details = {
            "wasserstein_distance": dist,
            "reservoir_size": len(self.reservoir.values),
            "capacity": self.capacity,
            "n_seen": self.n,
            "approximation": "reservoir",
        }
        if meta:
            details.update(meta)

        return DriftResult(
            method="wasserstein",
            score=dist,
            threshold=threshold,
            sample_size=self.n,
            details=details,
        )

    def to_state(self) -> Dict[str, Any]:
        """Serialize state into msgpack-safe dictionary."""
        return {
            "algo": self.algo,
            "capacity": int(self.capacity),
            "reservoir": self.reservoir.to_state(),
            "n": int(self.n),
        }

    @classmethod
    def from_state(cls, d: Dict[str, Any]) -> "StreamingWassersteinStat":
        """Reconstruct StreamingWassersteinStat from serialized state."""
        stat = cls(capacity=d["capacity"])
        stat.reservoir = ReservoirSample.from_state(d["reservoir"])
        stat.n = int(d["n"])
        return stat

    def reset(self) -> None:
        """Clear reservoir buffer and reset observation count."""
        self.reservoir.reset()
        self.n = 0
