# tests/streaming/test_rebalance_safety.py

import json
import os
import pytest
import numpy as np
import pandas as pd
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor
from etsi.watchdog.streaming.windows.keys import offset_key, win_key, wm_key


def test_rebalance_handoff_without_loss_or_double_counting(tmp_path):
    """
    Task 3.4 / Issue #51: Multi-partition rebalance safety.
    Consumer 1 processes half a window on Partition 0.
    Rebalance revokes Partition 0 from Consumer 1 and assigns to Consumer 2.
    Consumer 2 seeks from stored offset, rehydrates state, and completes the window.
    Asserts:
      - Total events evaluated = 50
      - Exactly 1 window flush
      - Zero state loss, zero double-counting
    """
    ref_path = tmp_path / "ref.csv"
    pd.DataFrame({"metric": np.linspace(0, 100, 500)}).to_csv(ref_path, index=False)
    log_path = tmp_path / "logs" / "rebalance_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["metric"],
        topic="metrics-topic",
        window_seconds=60.0,
        grace_seconds=10.0,
        min_events=50,
        log_path=str(log_path),
    )

    shared_store = InMemoryStateStore()
    topic = "metrics-topic"
    partition = 0
    base_ts = 1700000040.0  # epoch-aligned (1700000040 % 60 == 0)

    # 1. Prepare 50 events in window [1700000000, 1700000060)
    messages = []
    for i in range(50):
        val = 10.0 + (i % 5)
        payload = {"stream_id": "stream-p0", "event_ts": base_ts + i, "metric": val}
        messages.append(
            RawMessage(topic, partition, i, b"stream-p0", json.dumps(payload).encode("utf-8"))
        )
    # Plus 1 watermark-advancing event at base_ts + 75 to trigger flush
    flush_payload = {"stream_id": "stream-p0", "event_ts": base_ts + 75.0, "metric": 12.0}
    messages.append(
        RawMessage(topic, partition, 50, b"stream-p0", json.dumps(flush_payload).encode("utf-8"))
    )

    # 2. Consumer 1 consumes first 25 messages
    source1 = FakeEventSource(messages)
    monitor1 = StreamingMonitor(config=cfg, source=source1, store=shared_store)
    monitor1.dispatcher.start()

    # Initial assignment of partition 0 to Consumer 1
    source1.trigger_rebalance(revoked=[], assigned=[(topic, partition)])
    assert (topic, partition) in monitor1.assigned_partitions

    # Ingest 25 events
    events_read = monitor1.run_once(max_events=25)
    assert events_read == 25
    assert len(monitor1.engine.open_windows) == 1
    stat1 = list(monitor1.engine.open_windows.values())[0]
    assert stat1.n == 25

    # 3. Rebalance occurs: Partition 0 is revoked from Consumer 1
    source1.trigger_rebalance(revoked=[(topic, partition)], assigned=[])
    # Checkpoint should have persisted state to store, and monitor1 state cleared
    assert len(monitor1.engine.open_windows) == 0
    assert len(monitor1.assigned_partitions) == 0
    monitor1.stop()

    # Verify StateStore contains persisted window state and offset 25
    k_off = offset_key(cfg.group, topic, partition)
    assert shared_store.get(k_off) is not None

    # 4. Consumer 2 is created and Partition 0 is assigned to Consumer 2
    source2 = FakeEventSource(messages)
    monitor2 = StreamingMonitor(config=cfg, source=source2, store=shared_store)
    monitor2.dispatcher.start()

    # Trigger on_assign on Consumer 2
    source2.trigger_rebalance(revoked=[], assigned=[(topic, partition)])
    assert (topic, partition) in monitor2.assigned_partitions

    # Verify Consumer 2 rehydrated the open window with n=25
    assert len(monitor2.engine.open_windows) == 1
    stat2 = list(monitor2.engine.open_windows.values())[0]
    assert stat2.n == 25

    # Verify Consumer 2 cursor was seeked to offset 25
    assert source2.cursor == 25

    # 5. Consumer 2 consumes remaining 26 messages (events 25..49 + flush event)
    while monitor2.run_once(max_events=30) > 0:
        pass

    monitor2.stop()

    # 6. Verify result in drift log
    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) == 1
    assert df_out.iloc[0]["stream_id"] == "stream-p0"
    assert df_out.iloc[0]["sample_size"] == 50  # Exactly 50 events!


def test_rebalance_count_based_window_handoff(tmp_path):
    """
    Verify rebalance safety for count-based tumbling windows (Issue #53 + #51).
    Consumer 1 processes 30 events of a 50-event count window.
    Rebalance moves partition to Consumer 2, which completes the remaining 20.
    """
    ref_path = tmp_path / "ref_count.csv"
    pd.DataFrame({"temp": np.linspace(15, 30, 200)}).to_csv(ref_path, index=False)
    log_path = tmp_path / "logs" / "rebalance_count.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["temp"],
        topic="count-topic",
        window_size_events=50,
        min_events=50,
        log_path=str(log_path),
    )

    shared_store = InMemoryStateStore()
    topic = "count-topic"
    partition = 0

    messages = []
    for i in range(50):
        payload = {"stream_id": "sensor-1", "event_ts": 1700000000.0 + i, "temp": 20.0 + (i % 3)}
        messages.append(
            RawMessage(topic, partition, i, b"sensor-1", json.dumps(payload).encode("utf-8"))
        )

    # Consumer 1 processes 30 events
    source1 = FakeEventSource(messages)
    monitor1 = StreamingMonitor(config=cfg, source=source1, store=shared_store)
    monitor1.dispatcher.start()
    source1.trigger_rebalance(revoked=[], assigned=[(topic, partition)])

    monitor1.run_once(max_events=30)
    assert monitor1.engine.stream_counts["sensor-1"] == 30

    # Rebalance: revoke from 1
    source1.trigger_rebalance(revoked=[(topic, partition)], assigned=[])
    assert len(monitor1.engine.open_windows) == 0
    monitor1.stop()

    # Consumer 2 takes partition 0
    source2 = FakeEventSource(messages)
    monitor2 = StreamingMonitor(config=cfg, source=source2, store=shared_store)
    monitor2.dispatcher.start()
    source2.trigger_rebalance(revoked=[], assigned=[(topic, partition)])

    # Verify rehydrated count and slice
    assert monitor2.engine.stream_counts["sensor-1"] == 30
    assert ("sensor-1", "temp", "c:0") in monitor2.engine.open_windows
    assert source2.cursor == 30

    # Consumer 2 processes remaining 20 events
    while monitor2.run_once(max_events=30) > 0:
        pass
    monitor2.stop()

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) == 1
    assert df_out.iloc[0]["window_start"] == "c:0"
    assert df_out.iloc[0]["window_end"] == "c:50"
    assert df_out.iloc[0]["sample_size"] == 50


def test_multi_partition_selective_revocation(tmp_path):
    """
    Consumer owns Partitions 0 and 1.
    Rebalance revokes Partition 1 while keeping Partition 0.
    Partition 0 continues running without disruption; Partition 1 state is evicted locally.
    """
    ref_path = tmp_path / "ref_multi.csv"
    pd.DataFrame({"val": [1.0, 2.0, 3.0, 4.0, 5.0] * 50}).to_csv(ref_path, index=False)
    log_path = tmp_path / "logs" / "multi_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["val"],
        topic="multi-topic",
        window_size_events=50,
        log_path=str(log_path),
    )

    shared_store = InMemoryStateStore()
    topic = "multi-topic"

    p0_msgs = [
        RawMessage(topic, 0, i, b"stream-A", json.dumps({"stream_id": "stream-A", "event_ts": 1000 + i, "val": 2.0}).encode())
        for i in range(20)
    ]
    p1_msgs = [
        RawMessage(topic, 1, i, b"stream-B", json.dumps({"stream_id": "stream-B", "event_ts": 1000 + i, "val": 3.0}).encode())
        for i in range(20)
    ]
    all_msgs = p0_msgs + p1_msgs

    source = FakeEventSource(all_msgs)
    monitor = StreamingMonitor(config=cfg, source=source, store=shared_store)
    monitor.dispatcher.start()

    # Assign both partitions
    source.trigger_rebalance(revoked=[], assigned=[(topic, 0), (topic, 1)])
    monitor.run_once(max_events=40)

    assert "stream-A" in monitor.known_streams
    assert "stream-B" in monitor.known_streams

    # Revoke partition 1 only
    source.trigger_rebalance(revoked=[(topic, 1)], assigned=[])
    assert (topic, 0) in monitor.assigned_partitions
    assert (topic, 1) not in monitor.assigned_partitions
    assert "stream-A" in monitor.known_streams
    assert "stream-B" not in monitor.known_streams
    assert ("stream-B", "val", "c:0") not in monitor.engine.open_windows
    assert ("stream-A", "val", "c:0") in monitor.engine.open_windows

    monitor.stop()


def test_real_dataset_california_housing_rebalance_safety(tmp_path):
    """
    Empirical integration test: Partition handoff across consumers using the
    California Housing Census dataset.
    """
    from sklearn.datasets import fetch_california_housing

    data = fetch_california_housing(as_frame=True).frame
    ref_path = tmp_path / "ref_housing.csv"
    data.iloc[:500][["MedInc"]].to_csv(ref_path, index=False)
    log_path = tmp_path / "logs" / "housing_rebalance.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["MedInc"],
        topic="housing-topic",
        window_size_events=60,
        min_events=50,
        log_path=str(log_path),
    )

    shared_store = InMemoryStateStore()
    topic = "housing-topic"
    partition = 0

    messages = []
    base_ts = 1700000000.0
    for i in range(60):
        row = data.iloc[500 + i]
        payload = {"stream_id": "ca-housing", "event_ts": base_ts + i, "MedInc": float(row["MedInc"])}
        messages.append(
            RawMessage(topic, partition, i, b"ca-housing", json.dumps(payload).encode("utf-8"))
        )

    # Consumer 1 processes 35 events
    source1 = FakeEventSource(messages)
    monitor1 = StreamingMonitor(config=cfg, source=source1, store=shared_store)
    monitor1.dispatcher.start()
    source1.trigger_rebalance(revoked=[], assigned=[(topic, partition)])

    monitor1.run_once(max_events=35)
    assert monitor1.engine.stream_counts["ca-housing"] == 35

    # Rebalance: revoke partition 0 from consumer 1
    source1.trigger_rebalance(revoked=[(topic, partition)], assigned=[])
    monitor1.stop()

    # Consumer 2 takes over partition 0 and finishes the window
    source2 = FakeEventSource(messages)
    monitor2 = StreamingMonitor(config=cfg, source=source2, store=shared_store)
    monitor2.dispatcher.start()
    source2.trigger_rebalance(revoked=[], assigned=[(topic, partition)])

    while monitor2.run_once(max_events=30) > 0:
        pass
    monitor2.stop()

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) == 1
    assert df_out.iloc[0]["stream_id"] == "ca-housing"
    assert df_out.iloc[0]["sample_size"] == 60


def test_real_dataset_nasa_climate_rebalance_safety(tmp_path):
    """
    Empirical integration test: Partition handoff across consumers using the
    NASA Global Surface Temperature Anomaly dataset.
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
    log_path = tmp_path / "logs" / "nasa_rebalance.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["temp_anomaly"],
        topic="climate-topic",
        window_size_events=50,
        min_events=50,
        log_path=str(log_path),
    )

    shared_store = InMemoryStateStore()
    topic = "climate-topic"
    partition = 0

    messages = []
    base_ts = 1700000000.0
    for i, (_, row) in enumerate(modern.iloc[:50].iterrows()):
        payload = {"stream_id": "nasa-stream", "event_ts": base_ts + i, "temp_anomaly": float(row["temp_anomaly"])}
        messages.append(
            RawMessage(topic, partition, i, b"nasa-stream", json.dumps(payload).encode("utf-8"))
        )

    # Consumer 1 ingests 20 records
    source1 = FakeEventSource(messages)
    monitor1 = StreamingMonitor(config=cfg, source=source1, store=shared_store)
    monitor1.dispatcher.start()
    source1.trigger_rebalance(revoked=[], assigned=[(topic, partition)])

    monitor1.run_once(max_events=20)
    assert monitor1.engine.stream_counts["nasa-stream"] == 20

    # Rebalance: handoff to Consumer 2
    source1.trigger_rebalance(revoked=[(topic, partition)], assigned=[])
    monitor1.stop()

    source2 = FakeEventSource(messages)
    monitor2 = StreamingMonitor(config=cfg, source=source2, store=shared_store)
    monitor2.dispatcher.start()
    source2.trigger_rebalance(revoked=[], assigned=[(topic, partition)])

    while monitor2.run_once(max_events=30) > 0:
        pass
    monitor2.stop()

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) == 1
    assert df_out.iloc[0]["stream_id"] == "nasa-stream"
    assert df_out.iloc[0]["sample_size"] == 50
    assert df_out.iloc[0]["window_start"] == "c:0"
    assert df_out.iloc[0]["window_end"] == "c:50"


def test_rebalance_pending_marker_redispatched(tmp_path):
    """
    Verify that pending alert markers in StateStore are rehydrated and dispatched upon rebalance/restart.
    """
    from etsi.watchdog.streaming.state.codec import encode
    from etsi.watchdog.streaming.windows.keys import marker_key

    ref_path = tmp_path / "ref_marker.csv"
    pd.DataFrame({"metric": [1.0, 2.0, 3.0] * 50}).to_csv(ref_path, index=False)
    log_path = tmp_path / "logs" / "marker_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["metric"],
        topic="marker-topic",
        log_path=str(log_path),
    )

    shared_store = InMemoryStateStore()
    k_marker = marker_key(cfg.group, "stream-m", "metric", "psi", 1000.0)
    marker_payload = {
        "status": "pending",
        "marker": {
            "stream_id": "stream-m",
            "feature": "metric",
            "algo": "psi",
            "window_start": 1000.0,
            "window_end": 1060.0,
            "result": {
                "method": "psi",
                "score": 0.45,
                "threshold": 0.2,
                "sample_size": 100,
                "details": {"test": True},
            },
        },
    }
    shared_store.write_batch(puts={k_marker: encode(marker_payload)}, deletes=[])

    source = FakeEventSource([])
    monitor = StreamingMonitor(config=cfg, source=source, store=shared_store)
    monitor.dispatcher.start()

    # Rebalance assign triggers rehydration and redispatch
    source.trigger_rebalance(revoked=[], assigned=[("marker-topic", 0)])

    monitor.stop()

    assert log_path.exists()
    df_out = pd.read_csv(log_path)
    assert len(df_out) == 1
    assert df_out.iloc[0]["stream_id"] == "stream-m"
    assert df_out.iloc[0]["score"] == 0.45
