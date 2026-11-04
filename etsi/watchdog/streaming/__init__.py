# etsi/watchdog/streaming/__init__.py

"""
etsi.watchdog.streaming — Real-time event-driven streaming drift detection.
"""

from etsi.watchdog.drift.factory import register_drift_algorithm
from .stats.adapters import psi_streaming

# Register streaming algorithms dynamically when streaming module is imported (D-10)
register_drift_algorithm("psi_streaming", psi_streaming)

__all__ = [
    "psi_streaming",
]
