# etsi/watchdog/streaming/stats/adapters.py

import warnings
import pandas as pd
from typing import Optional
from etsi.watchdog.drift.base import DriftResult
from .reference import ReferenceProfile
from .psi_incremental import PsiStat


def psi_streaming(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    feature: str,
    threshold: float = 0.2,
) -> Optional[DriftResult]:
    """
    DataFrame-signature adapter for incremental streaming PSI.
    Replays current_df through PsiStat against fixed reference bin edges.
    Enables DriftCheck, Monitor, and existing batch APIs to execute streaming PSI.
    """
    if feature not in reference_df.columns or feature not in current_df.columns:
        warnings.warn(f"[etsi-watchdog] Skipping '{feature}' — missing in one of the datasets.")
        return None

    if not pd.api.types.is_numeric_dtype(reference_df[feature]):
        warnings.warn(f"[etsi-watchdog] Skipping '{feature}' — PSI requires numeric data.")
        return None

    ref_series = reference_df[feature].dropna()
    cur_series = current_df[feature].dropna()

    if len(cur_series) < 50:
        warnings.warn(
            f"[etsi-watchdog] Sample size too small for reliable PSI (<50): {len(cur_series)}",
            stacklevel=2,
        )

    ref_profile = ReferenceProfile(ref_series.values, feature=feature, buckets=10)
    stat = PsiStat(buckets=ref_profile.buckets, edges=ref_profile.edges_list)

    for x in cur_series.values:
        stat.update(float(x))

    return stat.finalize(ref_profile, threshold=threshold)


# Metadata attributes required by the streaming runtime (D-10)
psi_streaming.stat_factory = PsiStat
psi_streaming.default_threshold = 0.2
