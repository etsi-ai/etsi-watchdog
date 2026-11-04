# etsi/watchdog/streaming/stats/psi_incremental.py

import bisect
import math
from typing import List, Dict, Any, Optional
from etsi.watchdog.drift.base import DriftResult
from .base import StreamingStat
from .reference import ReferenceProfile


class PsiStat(StreamingStat):
    """
    Incremental Population Stability Index (PSI) statistic.
    Uses fixed bin edges computed once from reference profile.
    Maintains integer counts updated in O(1) via bisect.bisect_right on a Python list.
    Out-of-range values clamp into the edge bins.
    """
    algo: str = "psi"

    def __init__(self, buckets: int, edges: List[float]):
        self.buckets: int = buckets
        self.edges_list: List[float] = [float(e) for e in edges]
        self.counts: List[int] = [0] * buckets
        self.n: int = 0

    def update(self, x: float) -> None:
        """Increment count for the bin corresponding to x, clamped to [0, buckets-1]."""
        i = bisect.bisect_right(self.edges_list, x) - 1
        i = min(max(i, 0), self.buckets - 1)
        self.counts[i] += 1
        self.n += 1

    def merge(self, other: "StreamingStat", rng_seed: int = 0) -> "PsiStat":
        """Merge two PsiStat objects with matching bin edges by adding counts."""
        if not isinstance(other, PsiStat):
            raise TypeError(f"Cannot merge PsiStat with {type(other).__name__}")
        if self.buckets != other.buckets:
            raise ValueError(f"Bucket mismatch: {self.buckets} vs {other.buckets}")

        merged = PsiStat(self.buckets, self.edges_list)
        merged.counts = [a + b for a, b in zip(self.counts, other.counts)]
        merged.n = self.n + other.n
        return merged

    def finalize(
        self,
        ref: ReferenceProfile,
        threshold: float = 0.2,
        meta: Optional[Dict[str, Any]] = None
    ) -> DriftResult:
        """
        Calculate final PSI score against the reference profile and construct DriftResult.
        Mirrors the zero-mass rule: if e == 0 or a == 0, contribution is 0.
        """
        if self.n == 0:
            actual_percents = [0.0] * self.buckets
            psi_values = [0.0] * self.buckets
            score = 0.0
        else:
            actual_percents = [float(c / self.n) for c in self.counts]
            psi_values = []
            for e, a in zip(ref.ref_percents, actual_percents):
                if e == 0.0 or a == 0.0:
                    psi_values.append(0.0)
                else:
                    psi_values.append(float((e - a) * math.log(e / a)))
            score = float(sum(psi_values))

        details = {
            "bins": [
                f"{ref.edges_list[i]:.2f}-{ref.edges_list[i+1]:.2f}"
                for i in range(self.buckets)
            ],
            "expected_percents": list(ref.ref_percents),
            "actual_percents": actual_percents,
            "psi_per_bin": psi_values,
        }
        if meta:
            details.update(meta)

        return DriftResult(
            method="psi",
            score=score,
            threshold=threshold,
            sample_size=self.n,
            details=details,
        )

    def to_state(self) -> Dict[str, Any]:
        """Serialize state into msgpack-safe primitive dictionary."""
        return {
            "algo": self.algo,
            "buckets": self.buckets,
            "counts": [int(c) for c in self.counts],
            "n": int(self.n),
            "edges": [float(e) for e in self.edges_list],
        }

    @classmethod
    def from_state(cls, d: Dict[str, Any]) -> "PsiStat":
        """Reconstruct PsiStat from serialized dictionary."""
        stat = cls(d["buckets"], d["edges"])
        stat.counts = [int(c) for c in d["counts"]]
        stat.n = int(d["n"])
        return stat

    def reset(self) -> None:
        """Reset counts and event tally to zero."""
        self.counts = [0] * self.buckets
        self.n = 0
