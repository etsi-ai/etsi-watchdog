# etsi/watchdog/drift/wasserstein.py

from scipy.stats import wasserstein_distance
from .base import DriftResult
import warnings

def wasserstein_drift(reference_df, current_df, feature, threshold=0.1) -> DriftResult:
    ref = reference_df[feature].dropna().values
    cur = current_df[feature].dropna().values

    if len(cur) < 50:
        warnings.warn(f"[etsi-watchdog] Feature '{feature}' has few samples (<50): {len(cur)}", stacklevel=2)

    distance = wasserstein_distance(ref, cur)

    return DriftResult(
        method="wasserstein",
        score=distance,
        threshold=threshold,
        sample_size=len(cur),
        details={
            "wasserstein_distance": float(distance)
        }
    )
