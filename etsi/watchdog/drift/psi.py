# etsi/watchdog/drift/psi.py

import numpy as np
import warnings
from .base import DriftResult


def compute_psi(expected, actual, buckets=10, threshold=0.2):
    def scale_range(array):
        min_val, max_val = np.min(array), np.max(array)
        return min_val, max_val + 1e-8  # avoid div-by-zero

    min_val, max_val = scale_range(np.concatenate([expected, actual]))
    breakpoints = np.linspace(min_val, max_val, buckets + 1)

    expected_counts, _ = np.histogram(expected, bins=breakpoints)
    actual_counts, _ = np.histogram(actual, bins=breakpoints)

    expected_percents = expected_counts / len(expected)
    actual_percents = actual_counts / len(actual)

    psi_values = []
    for e, a in zip(expected_percents, actual_percents):
        if e == 0 or a == 0:
            psi_values.append(0)
        else:
            psi_values.append((e - a) * np.log(e / a))

    psi_score = np.sum(psi_values)

    return DriftResult(
        method="psi",
        score=psi_score,
        threshold=threshold,
        sample_size=len(actual),
        details={
            "bins": [f"{breakpoints[i]:.2f}-{breakpoints[i+1]:.2f}" for i in range(buckets)],
            "expected_percents": expected_percents.tolist(),
            "actual_percents": actual_percents.tolist(),
            "psi_per_bin": psi_values
        }
    )


def psi_drift(reference_df, current_df, feature, threshold=0.2):
    # Only process numeric data
    import pandas as pd
    if not pd.api.types.is_numeric_dtype(reference_df[feature].dtype):
        warnings.warn(f"[etsi-watchdog] Skipping '{feature}' — PSI requires numeric data.")
        return None

    ref = reference_df[feature].dropna().values
    cur = current_df[feature].dropna().values

    if len(cur) < 50:
        warnings.warn(f"[etsi-watchdog] Sample size too small for reliable PSI (<50): {len(cur)}", stacklevel=2)

    return compute_psi(ref, cur, threshold=threshold)
