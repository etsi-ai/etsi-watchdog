# tests/streaming/test_windows_count.py

import json
import os
import pytest
import numpy as np
import pandas as pd
from typing import Dict, Any

from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.stats.psi_incremental import PsiStat
from etsi.watchdog.streaming.windows.engine import WindowEngine, Event, Marker
from etsi.watchdog.streaming.windows.tumbling import CountTumblingWindowSlice
from etsi.watchdog.streaming.windows.keys import (
    win_key,
    win_count_key,
    marker_key,
    marker_count_key,
    stream_count_key,
    parse_win_key,
)
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor
from etsi.watchdog.streaming.checkpoint import CheckpointWriter
from etsi.watchdog.streaming.alerts import AlertDispatcher


def test_count_key_scheme_addressability():
    """
    Verify the count-based key scheme functions, addressability, and parsing.
    """
    group = "prod-group"
    stream_id = "sensor-station-01"
    feature = "temperature"
    count_start = 500

    # Key generation helpers
    k_win = win_count_key(group, stream_id, feature, count_start)
    assert k_win == f"wd:v1:{group}:win:{stream_id}:{feature}:c:{count_start}"

    k_marker = marker_count_key(group, stream_id, feature, "psi", count_start)
    assert k_marker == f"wd:v1:{group}:marker:{stream_id}:{feature}:psi:c:{count_start}"

    k_count = stream_count_key(group, stream_id)
    assert k_count == f"wd:v1:{group}:count:{stream_id}"

    # Bidirectional parsing
    parsed_count = parse_win_key(k_win)
    assert parsed_count["group"] == group
    assert parsed_count["stream_id"] == stream_id
    assert parsed_count["feature"] == feature
    assert parsed_count["window_type"] == "count"
    assert parsed_count["window_start"] == count_start

    # Time-based key parsing backward compatibility
    time_key = win_key(group, stream_id, feature, 1700000000.0)
    parsed_time = parse_win_key(time_key)
    assert parsed_time["window_type"] == "time"
    assert parsed_time["window_start"] == 1700000000.0


def test_count_tumbling_window_slice():
    """
    Verify CountTumblingWindowSlice boundary calculations and capacity check.
    """
    stat = PsiStat(buckets=10, edges=[float(i) for i in range(11)])
    slice_obj = CountTumblingWindowSlice(
        stream_id="stream-1",
        feature="feat_x",
        window_start=100,
        window_size_events=50,
        stat=stat,
    )

    assert slice_obj.window_start == 100
    assert slice_obj.window_end == 150
    assert not slice_obj.is_due()

    for i in range(49):
        stat.update(float(i % 10))
    assert not slice_obj.is_due()

    stat.update(5.0)
    assert slice_obj.is_due()
    assert CountTumblingWindowSlice.count_start(149, 50) == 100
    assert CountTumblingWindowSlice.count_start(150, 50) == 150


def test_window_engine_count_based_flushes_at_exact_event_capacity():
    """
    Verify WindowEngine in count-based mode flushes deterministically
    when window_size_events is reached.
    """
    ref = np.linspace(0, 100, 500)
    ref_prof = ReferenceProfile(ref, feature="f1", buckets=10)
    references = {"f1": ref_prof}

    engine = WindowEngine(
        window_size_events=20,
        stat_factory=lambda b, e: PsiStat(b, e),
        references=references,
        threshold=0.2,
        min_events=20,
        algo="psi",
    )
    assert engine.is_count_based is True

    # Ingest 19 events -> no flush
    for i in range(19):
        engine.apply(Event(stream_id="stream-1", ts=1000.0 + i, values={"f1": float(i)}))

    assert len(engine.pending_markers) == 0
    assert engine.windows_flushed == 0
    assert len(engine.open_windows) == 1

    # 20th event triggers flush of window [c:0, c:20)
    engine.apply(Event(stream_id="stream-1", ts=1020.0, values={"f1": 20.0}))
    assert len(engine.pending_markers) == 1
    assert engine.windows_flushed == 1

    m1 = engine.pending_markers[0]
    assert m1.stream_id == "stream-1"
    assert m1.feature == "f1"
    assert m1.window_start == "c:0"
    assert m1.window_end == "c:20"
    assert m1.result.sample_size == 20

    # Ingest 20 more events -> triggers second window flush [c:20, c:40)
    for i in range(20):
        engine.apply(Event(stream_id="stream-1", ts=1021.0 + i, values={"f1": float(i + 20)}))

    assert len(engine.pending_markers) == 2
    assert engine.windows_flushed == 2
    m2 = engine.pending_markers[1]
    assert m2.window_start == "c:20"
    assert m2.window_end == "c:40"


def test_window_engine_count_based_stream_isolation():
    """
    Verify event counts and window flushes are strictly isolated per stream.
    """
    ref = np.linspace(0, 50, 200)
    ref_prof = ReferenceProfile(ref, feature="metric", buckets=10)
    references = {"metric": ref_prof}

    engine = WindowEngine(
        window_size_events=30,
        stat_factory=lambda b, e: PsiStat(b, e),
        references=references,
        threshold=0.2,
        min_events=30,
        algo="psi",
    )

    # Stream A gets 30 events -> flushes
    for i in range(30):
        engine.apply(Event(stream_id="stream-A", ts=100.0 + i, values={"metric": float(i)}))

    # Stream B gets 15 events -> remains open
    for i in range(15):
        engine.apply(Event(stream_id="stream-B", ts=100.0 + i, values={"metric": float(i)}))

    assert engine.windows_flushed == 1
    assert len(engine.pending_markers) == 1
    assert engine.pending_markers[0].stream_id == "stream-A"
    assert engine.stream_counts["stream-A"] == 30
    assert engine.stream_counts["stream-B"] == 15
    assert ("stream-B", "metric", "c:0") in engine.open_windows


def test_count_based_checkpoint_writer_persists_count_keys():
    """
    Verify CheckpointWriter saves count keys and count-based window keys to state store.
    """
    ref = np.linspace(0, 100, 200)
    ref_prof = ReferenceProfile(ref, feature="metric", buckets=10)
    references = {"metric": ref_prof}

    engine = WindowEngine(
        window_size_events=50,
        stat_factory=lambda b, e: PsiStat(b, e),
        references=references,
        threshold=0.2,
        min_events=50,
        algo="psi",
    )

    store = InMemoryStateStore()
    dispatcher = AlertDispatcher(log_path="logs/test_drift.csv")
    writer = CheckpointWriter(group="grp", store=store, engine=engine, dispatcher=dispatcher)

    # Apply 25 events (partial window)
    for i in range(25):
        engine.apply(Event(stream_id="stream-1", ts=1.0 + i, values={"metric": float(i)}))

    writer.checkpoint(offsets={("test-topic", 0): 25})

    # State store must contain stream event count and active window state
    count_bytes = store.get(stream_count_key("grp", "stream-1"))
    assert count_bytes is not None

    win_bytes = store.get(win_count_key("grp", "stream-1", "metric", 0))
    assert win_bytes is not None


def test_config_validation_for_count_windows():
    """
    Verify configuration validation for count-based window options.
    """
    cfg_valid = StreamingConfig(
        reference_path="dummy.csv",
        features=["f1"],
        topic="topic",
        window_size_events=100,
    )
    assert cfg_valid.window_size_events == 100

    cfg_invalid = StreamingConfig(
        reference_path="dummy.csv",
        features=["f1"],
        topic="topic",
        window_size_events=0,
    )
    with pytest.raises(ValueError, match="WATCHDOG_WINDOW_SIZE_EVENTS must be > 0"):
        cfg_invalid.validate()


def test_real_dataset_california_housing_count_based_streaming(tmp_path):
    """
    Integration test verifying count-based streaming window evaluation on the
    empirical California Housing Census dataset.
    """
    from sklearn.datasets import fetch_california_housing

    data = fetch_california_housing(as_frame=True).frame
    ref_path = tmp_path / "ref_housing.csv"
    data.iloc[:500][["MedInc"]].to_csv(ref_path, index=False)

    log_path = tmp_path / "logs" / "housing_count_drift.csv"

    # Configure count-based windows of 60 events each
    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["MedInc"],
        topic="housing-stream",
        window_size_events=60,
        min_events=50,
        log_path=str(log_path),
    )

    # Ingest 120 empirical events (should trigger exactly 2 window flushes: 0-60 and 60-120)
    messages = []
    base_ts = 1700000000.0
    for i in range(120):
        row = data.iloc[500 + i]
        payload = {
            "stream_id": "ca-housing-stream",
            "event_ts": base_ts + i,
            "MedInc": float(row["MedInc"]),
        }
        messages.append(
            RawMessage("housing-stream", 0, i, b"ca-housing-stream", json.dumps(payload).encode("utf-8"))
        )

    source = FakeEventSource(messages)
    store = InMemoryStateStore()
    monitor = StreamingMonitor(config=cfg, source=source, store=store)
    monitor.dispatcher.start()

    while monitor.run_once(max_events=40) > 0:
        pass

    monitor.stop()

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) == 2

    # Check window 1 identity
    assert df_out.iloc[0]["stream_id"] == "ca-housing-stream"
    assert df_out.iloc[0]["window_start"] == "c:0"
    assert df_out.iloc[0]["window_end"] == "c:60"
    assert df_out.iloc[0]["sample_size"] == 60

    # Check window 2 identity
    assert df_out.iloc[1]["stream_id"] == "ca-housing-stream"
    assert df_out.iloc[1]["window_start"] == "c:60"
    assert df_out.iloc[1]["window_end"] == "c:120"
    assert df_out.iloc[1]["sample_size"] == 60


def test_real_dataset_nasa_climate_count_based_streaming(tmp_path):
    """
    Integration test verifying count-based streaming window evaluation on the
    empirical NASA Global Surface Temperature Anomaly dataset.
    """
    import scipy
    nasa_csv_path = os.path.join(os.path.dirname(scipy.__file__), "signal", "tests", "data", "GLB.Ts+dSST.csv")
    if not os.path.exists(nasa_csv_path):
        pytest.skip(f"NASA dataset not found at {nasa_csv_path}")

    df_nasa = pd.read_csv(nasa_csv_path, skiprows=1)
    df_nasa["Year"] = pd.to_numeric(df_nasa["Year"], errors="coerce")
    df_nasa["J-D"] = pd.to_numeric(df_nasa["J-D"], errors="coerce")
    df_clean = df_nasa.dropna(subset=["Year", "J-D"]).copy()

    ref_path = tmp_path / "ref_nasa.csv"
    baseline = df_clean[df_clean["Year"] <= 1940][["J-D"]].rename(columns={"J-D": "temp_anomaly"})
    baseline.to_csv(ref_path, index=False)

    modern = df_clean[df_clean["Year"] > 1940][["J-D"]].rename(columns={"J-D": "temp_anomaly"})
    log_path = tmp_path / "logs" / "nasa_count_drift.csv"

    # Configure count-based windows of 50 events each
    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["temp_anomaly"],
        topic="climate-stream",
        window_size_events=50,
        min_events=50,
        log_path=str(log_path),
    )

    messages = []
    base_ts = 1700000000.0
    for i, (_, row) in enumerate(modern.iloc[:70].iterrows()):
        payload = {
            "stream_id": "nasa-stream",
            "event_ts": base_ts + i,
            "temp_anomaly": float(row["temp_anomaly"]),
        }
        messages.append(
            RawMessage("climate-stream", 0, i, b"nasa-stream", json.dumps(payload).encode("utf-8"))
        )

    source = FakeEventSource(messages)
    store = InMemoryStateStore()
    monitor = StreamingMonitor(config=cfg, source=source, store=store)
    monitor.dispatcher.start()

    while monitor.run_once(max_events=30) > 0:
        pass

    monitor.stop()

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    # First 50 events flushes 1 window [c:0, c:50)
    assert len(df_out) == 1
    assert df_out.iloc[0]["stream_id"] == "nasa-stream"
    assert df_out.iloc[0]["window_start"] == "c:0"
    assert df_out.iloc[0]["window_end"] == "c:50"
    assert df_out.iloc[0]["sample_size"] == 50
