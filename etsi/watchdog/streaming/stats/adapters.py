# etsi/watchdog/streaming/stats/adapters.py

import warnings
import pandas as pd
from typing import Optional, Tuple
from etsi.watchdog.drift.base import DriftResult
from .reference import ReferenceProfile
from .psi_incremental import PsiStat
from .ks_streaming import StreamingKsStat
from .wasserstein_streaming import StreamingWassersteinStat


def _extract_numeric_series(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    feature: str,
    algo_label: str,
) -> Optional[Tuple[pd.Series, pd.Series]]:
    """Validate and extract non-null numeric reference and current Series for a feature."""
    if feature not in reference_df.columns or feature not in current_df.columns:
        warnings.warn(f"[etsi-watchdog] Skipping '{feature}' — missing in one of the datasets.")
        return None

    if not pd.api.types.is_numeric_dtype(reference_df[feature]):
        warnings.warn(f"[etsi-watchdog] Skipping '{feature}' — {algo_label} requires numeric data.")
        return None

    ref_series = reference_df[feature].dropna()
    cur_series = current_df[feature].dropna()

    if len(cur_series) < 50:
        warnings.warn(
            f"[etsi-watchdog] Sample size too small for reliable {algo_label} (<50): {len(cur_series)}",
            stacklevel=3,
        )

    return ref_series, cur_series


def psi_streaming(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    feature: str,
    threshold: float = 0.2,
    buckets: int = 10,
) -> Optional[DriftResult]:
    """
    DataFrame-signature adapter for incremental streaming PSI.
    Replays current_df through PsiStat against fixed reference bin edges.
    Enables DriftCheck, Monitor, and existing batch APIs to execute streaming PSI.
    """
    extracted = _extract_numeric_series(reference_df, current_df, feature, "PSI")
    if extracted is None:
        return None
    ref_series, cur_series = extracted

    ref_profile = ReferenceProfile(ref_series.values, feature=feature, buckets=buckets)
    stat = PsiStat(buckets=ref_profile.buckets, edges=ref_profile.edges_list)

    for x in cur_series.values:
        stat.update(float(x))

    return stat.finalize(ref_profile, threshold=threshold)


# Metadata attributes required by the streaming runtime (D-10)
psi_streaming.stat_factory = PsiStat
psi_streaming.default_threshold = 0.2


def ks_streaming(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    feature: str,
    threshold: float = 0.95,
) -> Optional[DriftResult]:
    """
    DataFrame-signature adapter for streaming Kolmogorov-Smirnov test via reservoir sampling (Task 1.2, 1.3).
    Replays current_df through StreamingKsStat against the reference profile.
    Default threshold is 0.95 (1 - pvalue, calibrated to control null false alarms).
    """
    extracted = _extract_numeric_series(reference_df, current_df, feature, "KS")
    if extracted is None:
        return None
    ref_series, cur_series = extracted

    ref_profile = ReferenceProfile(ref_series.values, feature=feature)
    stat = StreamingKsStat(capacity=2000)

    for x in cur_series.values:
        stat.update(float(x))

    return stat.finalize(ref_profile, threshold=threshold)


# Metadata attributes for streaming runtime
ks_streaming.stat_factory = StreamingKsStat
ks_streaming.default_threshold = 0.95


def wasserstein_streaming(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    feature: str,
    threshold: float = 0.1,
) -> Optional[DriftResult]:
    """
    DataFrame-signature adapter for streaming Wasserstein Distance via reservoir sampling (Task 1.2, 1.3).
    Replays current_df through StreamingWassersteinStat against the reference profile.
    """
    extracted = _extract_numeric_series(reference_df, current_df, feature, "Wasserstein")
    if extracted is None:
        return None
    ref_series, cur_series = extracted

    ref_profile = ReferenceProfile(ref_series.values, feature=feature)
    stat = StreamingWassersteinStat(capacity=2000)

    for x in cur_series.values:
        stat.update(float(x))

    return stat.finalize(ref_profile, threshold=threshold)


# Metadata attributes for streaming runtime
wasserstein_streaming.stat_factory = StreamingWassersteinStat
wasserstein_streaming.default_threshold = 0.1
