# etsi/watchdog/streaming/stats/reference.py

import numpy as np
from typing import List, Optional, Union, Sequence


class ReferenceProfile:
    """
    Precomputed reference profile for a feature.
    Contains fixed histogram bins, reference counts, and reference percentages
    computed once at service boot or initialization.
    """

    def __init__(
        self,
        values: Union[Sequence[float], np.ndarray],
        feature: str = "feature",
        buckets: int = 10,
        max_samples: int = 50000,
        seed: int = 42,
    ):
        self.feature = feature
        self.buckets = buckets

        arr = np.asarray(values, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if len(arr) == 0:
            raise ValueError(f"Reference data for '{feature}' has no finite values.")

        if len(arr) > max_samples:
            rng = np.random.default_rng(seed)
            indices = rng.choice(len(arr), size=max_samples, replace=False)
            arr = arr[indices]

        self.sample_size: int = len(arr)
        min_val = float(np.min(arr))
        max_val = float(np.max(arr))

        # Fix bin edges once from reference: min to max + 1e-8
        self.edges: np.ndarray = np.linspace(min_val, max_val + 1e-8, buckets + 1)
        self.edges_list: List[float] = [float(e) for e in self.edges]

        ref_counts, _ = np.histogram(arr, bins=self.edges)
        self.ref_counts: List[int] = [int(c) for c in ref_counts]
        self.ref_percents: List[float] = [float(c / self.sample_size) for c in self.ref_counts]
        self.sorted_sample: np.ndarray = np.sort(arr)
