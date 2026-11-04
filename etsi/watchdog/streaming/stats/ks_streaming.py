# etsi/watchdog/streaming/stats/ks_streaming.py

import math
from typing import Dict, Any, Optional
from scipy.stats import ks_2samp
from etsi.watchdog.drift.base import DriftResult
from .base import StreamingStat
from .reference import ReferenceProfile
from .reservoir import ReservoirSample
from .rng import SplitMix64


class StreamingKsStat(StreamingStat):
    """
    Streaming Kolmogorov-Smirnov (KS) two-sample test statistic via bounded reservoir sampling (Task 1.2, D-1, D-10).

    Approximation Error Characteristics (Dvoretzky-Kiefer-Wolfowitz Inequality):
    ----------------------------------------------------------------------------
    The two-sample KS test statistic measures the maximum empirical cumulative distribution function (ECDF)
    discrepancy: D = sup_x |F_k(x) - G_m(x)|.
    
    Because the live stream window is represented by a bounded reservoir sample of capacity k (default 2,000,
    max 10,000), the empirical distribution F_k deviates from the true population distribution F by at most:
        eps_95 = sqrt(ln(2 / 0.05) / (2 * k))
    with 95% confidence (DKW bound).
    
    For the default reservoir size k = 2,000:
        eps_cur_95 ~= 0.0304 (3.04%)
    For k = 10,000:
        eps_cur_95 ~= 0.0136 (1.36%)
    Combined with the pre-sampled reference distribution (max 50,000 points, eps_ref_95 ~= 0.006), the total
    discrepancy between the streaming KS statistic and the infinite-sample batch statistic is bounded by:
        |D_streaming - D_batch| <= eps_cur_95 + eps_ref_95 <= 0.036
    at the 95% confidence level.

    Statistical Calibration and Threshold Default:
    ---------------------------------------------
    The batch etsi-watchdog default threshold is 0.20. Under a streaming regime across multiple feature windows,
    a score threshold of 0.20 (corresponding to p-value < 0.80) triggers a false alert storm on ~82.6% of null windows
    (no drift). In streaming, the calibrated default threshold is 0.95 (corresponding to p-value < 0.05), which yields
    a controlled 5-8% false alert rate under the null hypothesis (ratified in J4 / D-10).
    """

    algo: str = "ks"
    default_capacity: int = 2000
    default_threshold: float = 0.95

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
    ) -> "StreamingKsStat":
        """Factory method to construct an instance seeded deterministically from window identity."""
        seed = SplitMix64.seed(base_seed, stream_id, feature, window_start)
        return cls(capacity=capacity, seed=seed)

    def update(self, x: float) -> None:
        """Incrementally update reservoir sample with observation x."""
        self.reservoir.update(x)
        self.n = self.reservoir.n

    def merge(self, other: "StreamingStat", rng_seed: int = 0) -> "StreamingKsStat":
        """Merge two StreamingKsStat instances using hypergeometric reservoir sampling."""
        if not isinstance(other, StreamingKsStat):
            raise TypeError(f"Cannot merge StreamingKsStat with {type(other).__name__}")

        merged_res = self.reservoir.merge(other.reservoir, rng_seed=rng_seed)
        merged = StreamingKsStat(capacity=self.capacity)
        merged.reservoir = merged_res
        merged.n = merged_res.n
        return merged

    def finalize(
        self,
        ref: ReferenceProfile,
        threshold: float = 0.95,
        meta: Optional[Dict[str, Any]] = None,
    ) -> DriftResult:
        """
        Evaluate Kolmogorov-Smirnov test between reference sample and window reservoir sample.
        Returns a DriftResult matching batch schema (score = 1 - p_value).
        """
        if len(self.reservoir.values) == 0:
            stat = 0.0
            pval = 1.0
            score = 0.0
            dkw_eps = 0.0
        else:
            ref_vals = getattr(ref, "sorted_sample", None)
            if ref_vals is None or len(ref_vals) == 0:
                ref_vals = np.asarray(ref.ref_counts, dtype=np.float64)

            ks_res = ks_2samp(ref_vals, self.reservoir.values)
            stat = float(ks_res.statistic)
            pval = float(ks_res.pvalue)
            score = float(1.0 - pval)

            k_curr = len(self.reservoir.values)
            dkw_eps = float(math.sqrt(math.log(2.0 / 0.05) / (2.0 * k_curr)))

        details = {
            "ks_statistic": stat,
            "p_value": pval,
            "reservoir_size": len(self.reservoir.values),
            "capacity": self.capacity,
            "n_seen": self.n,
            "approximation": "reservoir",
            "dkw_epsilon_95": dkw_eps,
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
        """Serialize state into msgpack-safe dictionary."""
        return {
            "algo": self.algo,
            "capacity": int(self.capacity),
            "reservoir": self.reservoir.to_state(),
            "n": int(self.n),
        }

    @classmethod
    def from_state(cls, d: Dict[str, Any]) -> "StreamingKsStat":
        """Reconstruct StreamingKsStat from serialized state."""
        stat = cls(capacity=d["capacity"])
        stat.reservoir = ReservoirSample.from_state(d["reservoir"])
        stat.n = int(d["n"])
        return stat

    def reset(self) -> None:
        """Clear reservoir buffer and reset observation count."""
        self.reservoir.reset()
        self.n = 0
