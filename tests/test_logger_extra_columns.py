# tests/test_logger_extra_columns.py

import os
import json
import pandas as pd
import pytest
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.logger import log_drift
from etsi.watchdog.streaming.windows.engine import Marker
from etsi.watchdog.streaming.alerts import AlertDispatcher
from etsi.watchdog.streaming.metrics import StreamingMetrics


def test_log_drift_backward_compatible(tmp_path):
    """Existing callers passing only result, path, and feature work unchanged."""
    csv_file = tmp_path / "legacy.csv"
    result = DriftResult(method="psi", score=0.15, threshold=0.2, details={}, sample_size=100)

    log_drift(result, str(csv_file), feature="feature_x")
    assert csv_file.exists()

    df = pd.read_csv(csv_file)
    assert len(df) == 1
    assert list(df.columns) == ["timestamp", "feature", "method", "score", "threshold", "drift", "sample_size"]
    assert df.iloc[0]["feature"] == "feature_x"
    assert df.iloc[0]["drift"] == False


def test_log_drift_bare_filename_does_not_crash(tmp_path, monkeypatch):
    """Verify Issue #49: bare filename with no directory component does not crash with FileNotFoundError."""
    monkeypatch.chdir(tmp_path)
    bare_file = "bare_drift.csv"

    result = DriftResult(method="psi", score=0.35, threshold=0.2, details={}, sample_size=50)
    # Must not raise FileNotFoundError: [Errno 2] No such file or directory: ''
    log_drift(result, bare_file, feature="temperature")

    assert os.path.exists(bare_file)
    df = pd.read_csv(bare_file)
    assert len(df) == 1
    assert df.iloc[0]["drift"] == True


def test_log_drift_with_extra_columns_csv_and_json(tmp_path):
    """Verify Issue #55: extra columns for stream_id and window identity."""
    csv_file = tmp_path / "extra_columns.csv"
    json_file = tmp_path / "extra_columns.json"

    result = DriftResult(method="ks", score=0.88, threshold=0.95, details={}, sample_size=2000)
    extra = {
        "stream_id": "production-cluster-01",
        "window_start": 1710000000.0,
        "window_end": 1710000060.0,
    }

    # 1. Test CSV format
    log_drift(result, str(csv_file), feature="latency_ms", extra=extra)
    assert csv_file.exists()

    df = pd.read_csv(csv_file)
    assert len(df) == 1
    assert "stream_id" in df.columns
    assert "window_start" in df.columns
    assert "window_end" in df.columns
    assert df.iloc[0]["stream_id"] == "production-cluster-01"
    assert df.iloc[0]["window_start"] == 1710000000.0
    assert df.iloc[0]["window_end"] == 1710000060.0

    # 2. Test JSON format
    log_drift(result, str(json_file), feature="latency_ms", extra=extra)
    assert json_file.exists()

    with open(json_file, "r") as f:
        records = json.load(f)
    assert len(records) == 1
    rec = records[0]
    assert rec["stream_id"] == "production-cluster-01"
    assert rec["window_start"] == 1710000000.0
    assert rec["window_end"] == 1710000060.0


def test_alert_dispatcher_writes_extra_identity_columns(tmp_path):
    """Verify AlertDispatcher dispatches markers with stream_id, window_start, and window_end columns."""
    log_file = tmp_path / "logs" / "dispatcher_extra.csv"
    dispatcher = AlertDispatcher(log_path=str(log_file), metrics=StreamingMetrics())
    dispatcher.start()

    marker = Marker(
        stream_id="stream-alpha",
        feature="med_income",
        algo="psi",
        window_start=100.0,
        window_end=160.0,
        result=DriftResult("psi", score=0.45, threshold=0.2, details={}, sample_size=120),
    )

    dispatcher.enqueue([marker])
    dispatcher.stop(timeout_s=5.0)

    assert log_file.exists()
    df = pd.read_csv(log_file)
    assert len(df) == 1
    assert "stream_id" in df.columns
    assert "window_start" in df.columns
    assert "window_end" in df.columns

    assert df.iloc[0]["stream_id"] == "stream-alpha"
    assert df.iloc[0]["window_start"] == 100.0
    assert df.iloc[0]["window_end"] == 160.0
    assert df.iloc[0]["drift"] == True


def test_log_drift_mixed_columns_dynamic_expansion(tmp_path):
    """
    Stress test sequential appends with heterogeneous extra columns:
    Row 1: legacy 7 columns
    Row 2: + stream_id
    Row 3: + stream_id, window_start, window_end
    Verifies that columns expand dynamically and all rows align without data corruption.
    """
    csv_file = tmp_path / "heterogeneous.csv"

    r1 = DriftResult(method="psi", score=0.1, threshold=0.2, details={}, sample_size=100)
    r2 = DriftResult(method="psi", score=0.3, threshold=0.2, details={}, sample_size=200)
    r3 = DriftResult(method="ks", score=0.8, threshold=0.95, details={}, sample_size=300)

    # Row 1: No extra
    log_drift(r1, str(csv_file), feature="f1")

    # Row 2: stream_id only
    log_drift(r2, str(csv_file), feature="f2", extra={"stream_id": "stream-1"})

    # Row 3: stream_id, window_start, window_end
    log_drift(r3, str(csv_file), feature="f3", extra={"stream_id": "stream-2", "window_start": 500.0, "window_end": 560.0})

    df = pd.read_csv(csv_file)
    assert len(df) == 3

    # Row 1 has NaN for stream_id, window_start, window_end
    assert pd.isna(df.iloc[0]["stream_id"])
    assert pd.isna(df.iloc[0]["window_start"])

    # Row 2 has stream_id, NaN for window_start
    assert df.iloc[1]["stream_id"] == "stream-1"
    assert pd.isna(df.iloc[1]["window_start"])

    # Row 3 has all fields populated
    assert df.iloc[2]["stream_id"] == "stream-2"
    assert df.iloc[2]["window_start"] == 500.0
    assert df.iloc[2]["window_end"] == 560.0


def test_log_drift_column_order_invariance(tmp_path):
    """Verify that dictionaries with extra keys passed in different order still align to the CSV columns."""
    csv_file = tmp_path / "order_invariance.csv"

    res = DriftResult(method="psi", score=0.25, threshold=0.2, details={}, sample_size=50)

    # Order A: col_x then col_y
    log_drift(res, str(csv_file), feature="feat", extra={"col_x": "val_x1", "col_y": "val_y1"})

    # Order B: col_y then col_x
    log_drift(res, str(csv_file), feature="feat", extra={"col_y": "val_y2", "col_x": "val_x2"})

    df = pd.read_csv(csv_file)
    assert len(df) == 2
    assert list(df["col_x"]) == ["val_x1", "val_x2"]
    assert list(df["col_y"]) == ["val_y1", "val_y2"]


def test_real_dataset_streaming_monitor_with_extra_columns(tmp_path):
    """
    Integration test using real California Housing empirical dataset streaming through StreamingMonitor.
    Verifies that all flushed drift results written to CSV contain stream_id, window_start, and window_end.
    """
    import json
    from sklearn.datasets import fetch_california_housing
    from etsi.watchdog.streaming.config import StreamingConfig
    from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
    from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
    from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor

    housing = fetch_california_housing(as_frame=True)
    df = housing.frame.copy()

    ref_path = tmp_path / "ref_housing.csv"
    df.iloc[:500][["MedInc"]].to_csv(ref_path, index=False)

    log_path = tmp_path / "logs" / "housing_identity_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["MedInc"],
        topic="housing-telemetry",
        window_seconds=20.0,
        grace_seconds=5.0,
        min_events=50,
        log_path=str(log_path),
    )

    # Generate 60 events in window 0 [0, 20), 60 events in window 1 [20, 40)
    messages = []
    base_ts = 1700000000.0
    for i in range(120):
        ts = base_ts + (i * 0.3)
        row = df.iloc[500 + i]
        payload = {
            "stream_id": "bay-area-real",
            "event_ts": ts,
            "MedInc": float(row["MedInc"]),
        }
        messages.append(RawMessage("housing-telemetry", 0, i, b"bay-area-real", json.dumps(payload).encode("utf-8")))

    # Trailing message to advance watermark and flush
    messages.append(
        RawMessage(
            "housing-telemetry",
            0,
            120,
            b"bay-area-real",
            json.dumps({"stream_id": "bay-area-real", "event_ts": base_ts + 50.0, "MedInc": 3.5}).encode("utf-8"),
        )
    )

    source = FakeEventSource(messages)
    store = InMemoryStateStore()
    monitor = StreamingMonitor(config=cfg, source=source, store=store)
    monitor.dispatcher.start()

    while monitor.run_once(max_events=50) > 0:
        pass

    monitor.dispatcher.stop(timeout_s=5.0)

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) >= 1

    # Invariant: Every flushed row must have identity columns
    assert "stream_id" in df_out.columns
    assert "window_start" in df_out.columns
    assert "window_end" in df_out.columns

    for _, r in df_out.iterrows():
        assert r["stream_id"] == "bay-area-real"
        assert r["window_end"] == r["window_start"] + 20.0



def test_real_dataset_nasa_climate_streaming_with_extra_columns(tmp_path):
    """
    Integration test using real NASA Global Surface Temperature Anomaly dataset (1880-present)
    streaming through StreamingMonitor with AlertDispatcher.
    Verifies that all flushed drift results written to CSV contain stream_id, window_start, and window_end.
    """
    import os
    import json
    import scipy
    nasa_csv_path = os.path.join(os.path.dirname(scipy.__file__), "signal", "tests", "data", "GLB.Ts+dSST.csv")
    if not os.path.exists(nasa_csv_path):
        pytest.skip(f"NASA dataset not found at {nasa_csv_path}")

    from etsi.watchdog.streaming.config import StreamingConfig
    from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
    from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
    from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor

    df_nasa = pd.read_csv(nasa_csv_path, skiprows=1)
    df_nasa["Year"] = pd.to_numeric(df_nasa["Year"], errors="coerce")
    df_nasa["J-D"] = pd.to_numeric(df_nasa["J-D"], errors="coerce")
    df_clean = df_nasa.dropna(subset=["Year", "J-D"]).copy()

    ref_path = tmp_path / "ref_nasa.csv"
    baseline = df_clean[df_clean["Year"] <= 1940][["J-D"]].rename(columns={"J-D": "temp_anomaly"})
    baseline.to_csv(ref_path, index=False)

    modern = df_clean[df_clean["Year"] > 1940][["J-D"]].rename(columns={"J-D": "temp_anomaly"})

    log_path = tmp_path / "logs" / "nasa_identity_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["temp_anomaly"],
        topic="climate-telemetry",
        window_seconds=30.0,
        grace_seconds=5.0,
        min_events=50,
        log_path=str(log_path),
    )

    messages = []
    base_ts = 1700000010.0
    for i, (_, row) in enumerate(modern.iloc[:70].iterrows()):
        payload = {
            "stream_id": "nasa-global-surface",
            "event_ts": base_ts + (i * 0.3),
            "temp_anomaly": float(row["temp_anomaly"]),
        }
        messages.append(
            RawMessage("climate-telemetry", 0, i, b"nasa-global-surface", json.dumps(payload).encode("utf-8"))
        )

    # Trailing message to advance watermark and flush
    messages.append(
        RawMessage(
            "climate-telemetry",
            0,
            70,
            b"nasa-global-surface",
            json.dumps({"stream_id": "nasa-global-surface", "event_ts": base_ts + 60.0, "temp_anomaly": 0.95}).encode("utf-8"),
        )
    )

    source = FakeEventSource(messages)
    store = InMemoryStateStore()
    monitor = StreamingMonitor(config=cfg, source=source, store=store)
    monitor.dispatcher.start()

    while monitor.run_once(max_events=50) > 0:
        pass

    monitor.dispatcher.stop(timeout_s=5.0)

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) >= 1

    assert "stream_id" in df_out.columns
    assert "window_start" in df_out.columns
    assert "window_end" in df_out.columns
    for _, r in df_out.iterrows():
        assert r["stream_id"] == "nasa-global-surface"
        assert r["window_end"] == r["window_start"] + 30.0
