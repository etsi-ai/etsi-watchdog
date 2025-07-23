# etsi/watchdog/__init__.py

"""
etsi.watchdog — Lightweight Drift Monitoring

Exposes:
- DriftCheck: core drift checker
- Monitor: rolling drift monitor
- DriftComparator: compare two drift runs
- DriftResult: result object with summary, plot, and JSON support
- SlackNotifier: Slack alert notifications for drift detection
- WatchdogConfig: Configuration helper for easy setup
- quick_setup: Convenience function for quick configuration
"""

from .drift_check import DriftCheck
from .monitor import Monitor
from .compare import DriftComparator
from .drift.base import DriftResult
from .slack_notifier import SlackNotifier
from .config import WatchdogConfig, quick_setup

__all__ = [
    "DriftCheck",
    "Monitor",
    "DriftComparator",
    "DriftResult",
    "SlackNotifier",
    "WatchdogConfig",
    "quick_setup",
]
