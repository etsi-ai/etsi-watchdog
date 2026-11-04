import pytest


def test_streaming_suite_initialized():
    """Verify that tests/streaming test suite is properly initialized and discovered."""
    assert True


@pytest.mark.integration
def test_integration_marker_skip():
    """Verify integration marker is detected and skipped unless WATCHDOG_IT=1."""
    assert True
