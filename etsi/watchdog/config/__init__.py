# etsi/watchdog/config/__init__.py
"""YAML Configuration module for etsi-watchdog."""

from .parser import ConfigParser
from .runner import ConfigRunner
from .schema import DriftConfig

__all__ = ['ConfigParser', 'ConfigRunner', 'DriftConfig']


def run_from_yaml(config_path: str):
    """Quick function to run drift detection from YAML."""
    runner = ConfigRunner()
    return runner.run_from_config(config_path)