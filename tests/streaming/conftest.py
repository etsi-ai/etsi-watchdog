import os
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: mark test as integration test requiring live services (opt-in via WATCHDOG_IT=1)"
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WATCHDOG_IT") == "1":
        # Do not skip integration tests when WATCHDOG_IT is enabled
        return

    skip_integration = pytest.mark.skip(reason="Integration test skipped unless WATCHDOG_IT=1")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
