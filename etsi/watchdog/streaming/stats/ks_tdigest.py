# etsi/watchdog/streaming/stats/ks_tdigest.py

"""
Streaming KS (Kolmogorov-Smirnov) statistic via t-digest quantile sketch (Issue #52).

Replaces the reservoir-based approach with a t-digest that provides superior tail
accuracy. Instead of storing raw samples and recomputing scipy.ks_2samp at flush,
the t-digest maintains a compressed distribution summary and computes the KS
statistic directly by walking both CDFs.

Error Bound Comparison (vs reservoir):
--------------------------------------
Reservoir (k=2,000, DKW bound):
    |D_streaming - D_batch| ≤ ε_cur + ε_ref ≈ 0.030 + 0.006 = 0.036  (95% CI)
    Memory: O(k) = 16 KB fixed

T-digest (δ=100):
    Tail quantile error: ≤ ~1/δ ≈ 1% relative at q < 0.01 or q > 0.99
    CDF error at tails: significantly better than reservoir (δ-dependent, not k-dependent)
    Memory: O(δ) ≈ 1.6 KB typical (100 centroids × 16 bytes)

The t-digest CDF is evaluated at a grid of points spanning the union of reference
and current support, and the KS statistic is the maximum absolute difference.

Merge Behavior:
    Two TDigestKsStat instances merge by merging their underlying t-digests.
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


class TDigestKsStat(StreamingStat):
    """
    Streaming KS two-sample test using t-digest CDF approximation.

    The KS statistic D = sup_x |F_ref(x) - F_cur(x)| is computed by evaluating
    both CDFs at a fine grid of points spanning the union of reference and current
    data support, then taking the maximum absolute difference.

    Default threshold is 0.95 (same as reservoir-based ks_streaming, calibrated
    per WORKFLOW_0 D-10 to control false alert rate to ~5-8%).
    """

    algo: str = "ks"
    default_compression: float = 100.0
    default_threshold: float = 0.95

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
    ) -> "TDigestKsStat":
        """Factory method matching the stat contract (D-1)."""
        return cls(compression=compression)

    def update(self, x: float) -> None:
        """Incrementally update t-digest with observation x. O(1) amortized."""
        self.digest.update(x)
        self.n = self.digest.n

    def merge(self, other: "StreamingStat", rng_seed: int = 0) -> "TDigestKsStat":
        """
        Merge two TDigestKsStat instances by merging underlying t-digests.
        The rng_seed parameter is accepted for stat contract compatibility
        but is not used (t-digest merge is deterministic).
        """
        if not isinstance(other, TDigestKsStat):
            raise TypeError(f"Cannot merge TDigestKsStat with {type(other).__name__}")

        merged = TDigestKsStat(compression=self.compression)
        merged.digest = self.digest.merge(other.digest)
        merged.n = merged.digest.n
        return merged

    def finalize(
        self,
        ref: ReferenceProfile,
        threshold: float = 0.95,
        meta: Optional[Dict[str, Any]] = None,
    ) -> DriftResult:
        """
        Compute KS statistic between reference and current t-digest CDF.

        The statistic is computed by evaluating both CDFs at a grid of points
        spanning the union of their supports. This avoids the O(n log n) sort
        required by scipy.ks_2samp and works directly on the compressed sketch.

        Score = 1 - p_value (approximated from the KS statistic and effective
        sample sizes, matching the reservoir-based approach).
        """
        self.digest._flush_buffer()

        if not self.digest.centroids or self.n == 0:
            stat_val = 0.0
            pval = 1.0
            score = 0.0
        else:
            ref_vals = getattr(ref, "sorted_sample", None)
            if ref_vals is None or len(ref_vals) == 0:
                ref_vals = np.asarray(ref.ref_counts, dtype=np.float64)

            # Build evaluation grid from reference quantiles and t-digest centroids
            # Use centroid means + reference sample quantiles for a thorough grid
            grid_points = set()

            # Add reference sample points (subsample if large)
            if len(ref_vals) > 500:
                indices = np.linspace(0, len(ref_vals) - 1, 500, dtype=int)
                for idx in indices:
                    grid_points.add(float(ref_vals[idx]))
            else:
                for v in ref_vals:
                    grid_points.add(float(v))

            # Add t-digest centroid means
            for c in self.digest.centroids:
                grid_points.add(c.mean)

            # Add min/max boundaries
            grid_points.add(self.digest._min)
            grid_points.add(self.digest._max)

            grid = sorted(grid_points)

            # Compute reference ECDF at grid points
            n_ref = len(ref_vals)
            ref_sorted = np.sort(ref_vals)

            max_diff = 0.0
            for x in grid:
                # Reference CDF: fraction of ref_vals <= x
                ref_cdf = float(np.searchsorted(ref_sorted, x, side="right")) / n_ref
                # Current CDF from t-digest
                cur_cdf = self.digest.cdf(x)
                diff = abs(ref_cdf - cur_cdf)
                if diff > max_diff:
                    max_diff = diff

            stat_val = max_diff

            # Approximate p-value using the Kolmogorov distribution
            # For two-sample KS: effective n = n1*n2/(n1+n2)
            n_eff = (n_ref * self.n) / (n_ref + self.n) if (n_ref + self.n) > 0 else 1
            # Kolmogorov approximation: P(D > d) ≈ 2*exp(-2*n_eff*d^2)
            # for large n_eff (asymptotic)
            if n_eff > 0 and stat_val > 0:
                lambda_val = (np.sqrt(n_eff) + 0.12 + 0.11 / np.sqrt(n_eff)) * stat_val
                # Kolmogorov distribution survival function (first few terms)
                pval = 0.0
                for k in range(1, 100):
                    term = 2.0 * ((-1.0) ** (k - 1)) * np.exp(-2.0 * k * k * lambda_val * lambda_val)
                    pval += term
                    if abs(term) < 1e-15:
                        break
                pval = max(0.0, min(1.0, pval))
            else:
                pval = 1.0

            score = float(1.0 - pval)

        details = {
            "ks_statistic": stat_val,
            "p_value": pval,
            "compression": self.compression,
            "centroid_count": len(self.digest.centroids),
            "n_seen": self.n,
            "approximation": "tdigest",
            "tdigest_memory_bytes": self.digest.memory_bytes,
        }
        if meta:
            details.update(meta)

        return DriftResult(
            method="ks",
            score=score,
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
    def from_state(cls, d: Dict[str, Any]) -> "TDigestKsStat":
        """Reconstruct from serialized state (D-1 stat contract)."""
        stat = cls(compression=d["compression"])
        stat.digest = TDigest.from_state(d["digest"])
        stat.n = int(d["n"])
        return stat

    def reset(self) -> None:
        """Reset digest and observation count."""
        self.digest.reset()
        self.n = 0
