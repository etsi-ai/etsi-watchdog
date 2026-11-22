# etsi/watchdog/streaming/stats/wasserstein_tdigest.py

"""
Streaming Wasserstein Distance (Earth Mover's Distance) via t-digest quantile sketch (Issue #52).

Replaces the reservoir-based approach with a t-digest that provides superior tail
accuracy. Instead of storing raw samples and recomputing scipy.wasserstein_distance
at flush, the t-digest maintains a compressed distribution summary and computes
the 1-Wasserstein distance via quantile function integration.

The 1-Wasserstein distance between distributions P and Q is:
    W_1(P, Q) = ∫₀¹ |F_P⁻¹(t) - F_Q⁻¹(t)| dt

This is approximated by evaluating both quantile functions at a uniform grid of
points in [0, 1] and computing a numerical integral (trapezoidal rule).

Error Bound Comparison (vs reservoir):
--------------------------------------
Reservoir (k=2,000):
    Empirical convergence rate O(k^{-1/2}) for distributions with finite second moments.
    Memory: O(k) = 16 KB fixed.

T-digest (δ=100):
    Quantile error ≤ ~1/δ ≈ 1% relative at tails, ~0.5% at median.
    The Wasserstein integral error is bounded by the maximum quantile error times
    the data range: |W_tdigest - W_exact| ≤ range(data) / δ.
    Memory: O(δ) ≈ 1.6 KB typical (100 centroids × 16 bytes).

Merge Behavior:
    Two TDigestWassersteinStat instances merge by merging their underlying t-digests.
    The merged t-digest preserves the same error bounds as a fresh digest built
    from the concatenated stream. This enables correct hopping-window aggregation.

References:
    Dunning (2019), arXiv:1902.04023v1 — t-digest error bounds
    WORKFLOW_0.md §D-1 — stat contract, revisit trigger for tail accuracy
"""

import numpy as np
from typing import Dict, Any, Optional
from etsi.watchdog.drift.base import DriftResult
from .base import StreamingStat
from .reference import ReferenceProfile
from .tdigest import TDigest

# NumPy 2.0 renamed trapz to trapezoid; maintain compatibility across versions
_trapezoid = getattr(np, "trapezoid", getattr(np, "trapz", None))


class TDigestWassersteinStat(StreamingStat):
    """
    Streaming Wasserstein Distance using t-digest quantile function integration.

    The 1-Wasserstein distance is computed by evaluating both quantile functions
    (reference and current t-digest) at a grid of points in [0, 1] and
    integrating |Q_ref(t) - Q_cur(t)| via trapezoidal rule.

    Default threshold is 0.1 (same as reservoir-based wasserstein_streaming).
    Per WORKFLOW_0 D-10: Wasserstein threshold is required (score is in raw
    feature units), but the default_threshold is provided for stat_factory compatibility.
    """

    algo: str = "wasserstein"
    default_compression: float = 100.0
    default_threshold: float = 0.1

    # Number of quantile evaluation points for the integral
    _QUANTILE_GRID_SIZE: int = 500

    def __init__(
        self,
        compression: float = 100.0,
        *args: Any,
        **kwargs: Any,
    ):
        # Guard against positional calls from legacy bucket-based signatures
        if compression <= 100 and len(args) > 0 and isinstance(args[0], list):
            compression = self.default_compression

        self.compression: float = float(compression)
        self.digest: TDigest = TDigest(compression=self.compression)
        self.n: int = 0

    @classmethod
    def create_stat(
        cls,
        ref_prof: Optional[ReferenceProfile] = None,
        stream_id: str = "",
        feature: str = "",
        window_start: float = 0.0,
        compression: float = 100.0,
        *args: Any,
        **kwargs: Any,
    ) -> "TDigestWassersteinStat":
        """Factory method matching the stat contract (D-1)."""
        return cls(compression=compression)

    def update(self, x: float) -> None:
        """Incrementally update t-digest with observation x. O(1) amortized."""
        self.digest.update(x)
        self.n = self.digest.n

    def merge(self, other: "StreamingStat", rng_seed: int = 0) -> "TDigestWassersteinStat":
        """
        Merge two TDigestWassersteinStat instances by merging underlying t-digests.
        The rng_seed parameter is accepted for stat contract compatibility
        but is not used (t-digest merge is deterministic).
        """
        if not isinstance(other, TDigestWassersteinStat):
            raise TypeError(f"Cannot merge TDigestWassersteinStat with {type(other).__name__}")

        merged = TDigestWassersteinStat(compression=self.compression)
        merged.digest = self.digest.merge(other.digest)
        merged.n = merged.digest.n
        return merged

    def finalize(
        self,
        ref: ReferenceProfile,
        threshold: float = 0.1,
        meta: Optional[Dict[str, Any]] = None,
    ) -> DriftResult:
        """
        Compute Wasserstein-1 distance between reference and current t-digest.

        Uses quantile function integration:
            W_1 = ∫₀¹ |Q_ref(t) - Q_cur(t)| dt

        approximated via trapezoidal rule on a uniform grid of _QUANTILE_GRID_SIZE points.
        """
        self.digest._flush_buffer()

        if not self.digest.centroids or self.n == 0:
            dist = 0.0
        else:
            ref_vals = getattr(ref, "sorted_sample", None)
            if ref_vals is None or len(ref_vals) == 0:
                ref_vals = np.asarray(ref.ref_counts, dtype=np.float64)

            ref_sorted = np.sort(ref_vals)
            n_ref = len(ref_sorted)

            # Build uniform quantile grid: (0, 1) exclusive to avoid boundary issues
            grid_size = self._QUANTILE_GRID_SIZE
            qs = np.linspace(1.0 / (grid_size + 1), grid_size / (grid_size + 1), grid_size)

            # Reference quantile function: use sorted sample with interpolation
            ref_indices = qs * (n_ref - 1)
            ref_quantiles = np.interp(ref_indices, np.arange(n_ref), ref_sorted)

            # Current quantile function from t-digest
            cur_quantiles = np.array([self.digest.quantile(float(q)) for q in qs])

            # Trapezoidal integration of |Q_ref - Q_cur| over [0, 1]
            diffs = np.abs(ref_quantiles - cur_quantiles)
            dq = qs[1] - qs[0]  # Uniform spacing
            if _trapezoid is not None:
                dist = float(_trapezoid(diffs, dx=dq))
            else:
                dist = float(np.mean(diffs))

        details = {
            "wasserstein_distance": dist,
            "compression": self.compression,
            "centroid_count": len(self.digest.centroids),
            "n_seen": self.n,
            "approximation": "tdigest",
            "tdigest_memory_bytes": self.digest.memory_bytes,
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
        """Serialize state into msgpack-safe dictionary (D-1 stat contract)."""
        return {
            "algo": self.algo,
            "approximation": "tdigest",
            "compression": float(self.compression),
            "digest": self.digest.to_state(),
            "n": int(self.n),
        }

    @classmethod
    def from_state(cls, d: Dict[str, Any]) -> "TDigestWassersteinStat":
        """Reconstruct from serialized state (D-1 stat contract)."""
        stat = cls(compression=d["compression"])
        stat.digest = TDigest.from_state(d["digest"])
        stat.n = int(d["n"])
        return stat

    def reset(self) -> None:
        """Reset digest and observation count."""
        self.digest.reset()
        self.n = 0
