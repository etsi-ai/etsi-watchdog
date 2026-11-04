# etsi/watchdog/streaming/__init__.py

"""
etsi.watchdog.streaming — Real-time event-driven streaming drift detection.
"""

from etsi.watchdog.drift.factory import register_drift_algorithm
from .stats.adapters import psi_streaming, ks_streaming, wasserstein_streaming

# Register streaming algorithms dynamically when streaming module is imported (D-10, Task 1.3)
register_drift_algorithm("psi_streaming", psi_streaming)
register_drift_algorithm("ks_streaming", ks_streaming)
register_drift_algorithm("wasserstein_streaming", wasserstein_streaming)

__all__ = [
    "psi_streaming",
    "ks_streaming",
    "wasserstein_streaming",
]
