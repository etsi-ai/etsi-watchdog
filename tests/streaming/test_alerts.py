# tests/streaming/test_alerts.py

import os
import time
import pytest
from unittest.mock import Mock
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.streaming.windows.engine import Marker
from etsi.watchdog.streaming.alerts import AlertDispatcher
from etsi.watchdog.streaming.metrics import StreamingMetrics


def test_alert_dispatcher_csv_logging_and_slack(tmp_path):
    """Verify dispatcher logs CSV and forwards drifted alerts to Slack."""
    log_file = tmp_path / "logs" / "drift.csv"
    mock_notifier = Mock()
    mock_notifier.send_drift_alert.return_value = True

    metrics = StreamingMetrics()
    dispatcher = AlertDispatcher(
        log_path=str(log_file),
        notifier=mock_notifier,
        metrics=metrics,
    )
    dispatcher.start()

    # Create one drifted result and one non-drifted result
    m_drift = Marker(
        stream_id="stream-1",
        feature="featA",
        algo="psi",
        window_start=100.0,
        window_end=160.0,
        result=DriftResult("psi", score=0.6, threshold=0.2, details={}, sample_size=100),
    )
    m_no_drift = Marker(
        stream_id="stream-1",
        feature="featB",
        algo="psi",
        window_start=100.0,
        window_end=160.0,
        result=DriftResult("psi", score=0.05, threshold=0.2, details={}, sample_size=100),
    )

    dispatcher.enqueue([m_drift, m_no_drift])
    dispatcher.stop(timeout_s=5.0)

    # 1. Verify CSV log file exists and contains rows for both
    assert log_file.exists()
    content = log_file.read_text()
    assert "stream-1/featA" in content
    assert "stream-1/featB" in content

    # 2. Verify Slack alert was called ONLY for the drifted feature
    assert mock_notifier.send_drift_alert.call_count == 1
    assert metrics.alerts_sent == 1
    assert metrics.alerts_failed == 0


def test_alert_dispatcher_retry_on_failure(tmp_path):
    """Verify exponential backoff retry on temporary Slack errors."""
    log_file = tmp_path / "logs" / "retry_test.csv"
    mock_notifier = Mock()
    # Fails first 2 times, succeeds on 3rd attempt
    mock_notifier.send_drift_alert.side_effect = [Exception("503 Service Unavailable"), Exception("503"), True]

    metrics = StreamingMetrics()
    dispatcher = AlertDispatcher(
        log_path=str(log_file),
        notifier=mock_notifier,
        metrics=metrics,
    )
    dispatcher.start()

    m = Marker(
        stream_id="s1",
        feature="f1",
        algo="psi",
        window_start=0.0,
        window_end=60.0,
        result=DriftResult("psi", score=0.9, threshold=0.2, details={}, sample_size=50),
    )

    dispatcher.enqueue([m])
    dispatcher.stop(timeout_s=10.0)

    assert mock_notifier.send_drift_alert.call_count == 3
    assert metrics.alerts_sent == 1
    assert metrics.alerts_failed == 0
