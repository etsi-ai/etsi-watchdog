# etsi/watchdog/__init__.py

"""
etsi.watchdog — v3.0.0
Real-time Data Drift Detection & Monitoring
"""

import threading
import importlib
from typing import TYPE_CHECKING

__version__ = "3.0.0"

__all__ = [
    "DriftCheck",
    "Monitor",
    "DriftComparator",
    "DriftResult",
    "SlackNotifier",
    "WatchdogConfig",
    "quick_setup",
    "IsolationForestModel",
    "register_drift_algorithm",
    "get_drift_function",
]

_LAZY_IMPORTS = {
    "DriftCheck": (".drift_check", "DriftCheck"),
    "Monitor": (".monitor", "Monitor"),
    "DriftComparator": (".compare", "DriftComparator"),
    "DriftResult": (".drift.base", "DriftResult"),
    "register_drift_algorithm": (".drift.factory", "register_drift_algorithm"),
    "get_drift_function": (".drift.factory", "get_drift_function"),
    "SlackNotifier": (".slack_notifier", "SlackNotifier"),
    "WatchdogConfig": (".config", "WatchdogConfig"),
    "quick_setup": (".config", "quick_setup"),
    "IsolationForestModel": (".models.isolation_forest", "IsolationForestModel"),
}

_lock = threading.RLock()

if TYPE_CHECKING:
    from .drift_check import DriftCheck
    from .monitor import Monitor
    from .compare import DriftComparator
    from .drift.base import DriftResult
    from .drift.factory import register_drift_algorithm, get_drift_function
    from .slack_notifier import SlackNotifier
    from .config import WatchdogConfig, quick_setup
    from .models.isolation_forest import IsolationForestModel


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        with _lock:
            if name in globals():
                return globals()[name]
            module_path, attr_name = _LAZY_IMPORTS[name]
            module = importlib.import_module(module_path, __package__)
            val = getattr(module, attr_name)
            globals()[name] = val
            return val
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__():
    return sorted(list(globals().keys()) + __all__)
