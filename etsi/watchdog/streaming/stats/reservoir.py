# etsi/watchdog/streaming/stats/reservoir.py

from typing import List, Dict, Any, Optional
import numpy as np
from .rng import SplitMix64


class ReservoirSample:
    """
    Bounded-memory reservoir sampler using Algorithm R.
    Maintains an unbiased uniform random sample of at most k elements from an unbounded data stream.

    Guarantees:
    - O(1) incremental update time per observation.
    - Memory footprint strictly bounded to O(k) float64 values (max 10,000, default 2,000).
    - Every seen item has equal probability min(1, k / n) of inclusion in the reservoir.
    - Deterministic PRNG progression via SplitMix64 with reproducible serialization.
    """

    def __init__(
        self,
        k: int = 2000,
        seed: Optional[int] = None,
        rng: Optional[SplitMix64] = None,
    ):
        if k <= 0:
            raise ValueError(f"Reservoir capacity k must be positive, got {k}")
        if k > 10000:
            raise ValueError(f"Reservoir capacity k cannot exceed 10000 (D-1, J3), got {k}")

        self.k: int = int(k)
        self.n: int = 0
        self.values: List[float] = []

        if rng is not None:
            self.rng: SplitMix64 = rng
        else:
            self.rng = SplitMix64(0 if seed is None else seed)

    def update(self, x: float) -> None:
        """
        Incrementally ingest observation x via Algorithm R.
        For the first k items, append directly.
        For item n > k, replace a random slot j in [0, n-1] with probability k / n.
        """
        self.n += 1
        val = float(x)
        if len(self.values) < self.k:
            self.values.append(val)
        else:
            j = self.rng.next_int(self.n)
            if j < self.k:
                self.values[j] = val

    def merge(self, other: "ReservoirSample", rng_seed: int = 0) -> "ReservoirSample":
        """
        Merge two reservoir samples of capacities k with item counts n_a and n_b.
        Uses hypergeometric distribution sampling to preserve exact joint sample probabilities
        rather than naive concatenation (which skews toward the smaller stream).
        """
        if self.k != other.k:
            raise ValueError(f"Cannot merge reservoirs with different capacities: {self.k} vs {other.k}")

        merged_n = self.n + other.n
        merged = ReservoirSample(k=self.k, seed=rng_seed)
        merged.n = merged_n

        if merged_n == 0:
            return merged

        k_target = min(self.k, merged_n)

        # If both sample lists fit completely in k_target, take all without sampling
        if len(self.values) + len(other.values) <= k_target:
            merged.values = list(self.values) + list(other.values)
            return merged

        # Hypergeometric merge (PR-11, D-1):
        # Sample m_a elements from reservoir a, and k_target - m_a from reservoir b.
        np_rng = np.random.default_rng(rng_seed)
        m_a = int(np_rng.hypergeometric(ngood=self.n, nbad=other.n, nsample=k_target))
        m_b = k_target - m_a

        chosen_a: List[float] = []
        if m_a > 0 and len(self.values) > 0:
            take_a = min(m_a, len(self.values))
            idx_a = np_rng.choice(len(self.values), size=take_a, replace=False)
            chosen_a = [self.values[i] for i in idx_a]

        chosen_b: List[float] = []
        if m_b > 0 and len(other.values) > 0:
            take_b = min(m_b, len(other.values))
            idx_b = np_rng.choice(len(other.values), size=take_b, replace=False)
            chosen_b = [other.values[i] for i in idx_b]

        merged.values = chosen_a + chosen_b
        return merged

    def to_state(self) -> Dict[str, Any]:
        """Serialize state into msgpack-safe primitive dictionary."""
        return {
            "k": int(self.k),
            "n": int(self.n),
            "values": [float(v) for v in self.values],
            "rng_state": int(self.rng.state),
        }

    @classmethod
    def from_state(cls, d: Dict[str, Any]) -> "ReservoirSample":
        """Reconstruct ReservoirSample from serialized state dictionary."""
        sample = cls(k=d["k"], rng=SplitMix64(d["rng_state"]))
        sample.n = int(d["n"])
        sample.values = [float(v) for v in d["values"]]
        return sample

    def reset(self) -> None:
        """Clear reservoir buffer and reset observation count."""
        self.values = []
        self.n = 0
