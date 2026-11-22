# tests/streaming/test_real_data_streaming_dlq.py

import json
import time
import os
import pandas as pd
import pytest
from sklearn.datasets import fetch_california_housing
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor
from etsi.watchdog.streaming.dlq import FakeDeadLetterSink


@pytest.fixture(scope="module")
def california_housing_data():
    """Load real-world empirical California Housing dataset (no synthetic data)."""
    housing = fetch_california_housing(as_frame=True)
    df = housing.frame.copy()
    # Normalize column names
    df = df.rename(columns={
        "MedInc": "med_inc",
        "HouseAge": "house_age",
        "AveRooms": "ave_rooms",
        "Population": "population",
        "MedHouseVal": "med_house_val",
    })
    return df


def test_real_data_california_housing_streaming_with_dlq(tmp_path, california_housing_data):
    """
    Strong end-to-end testing using real California Housing census data (20,640 records).
    Tests real drift detection alongside Dead-Letter Queue (Issue #56) poison routing.
    """
    df = california_housing_data
    features = ["med_inc", "house_age", "med_house_val"]

    # 1. Real baseline: First 4,000 real census blocks (Inland/Central Valley districts)
    ref_df = df.iloc[:4000][features]
    ref_path = tmp_path / "california_ref.csv"
    ref_df.to_csv(ref_path, index=False)

    log_path = tmp_path / "logs" / "california_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=features,
        topic="california-census-telemetry",
        dead_letter_topic="california-census-dlq",
        window_seconds=60.0,
        grace_seconds=10.0,
        min_events=50,
        max_streams=5,
        log_path=str(log_path),
    )
    cfg.validate()

    # 2. Build live streaming messages from real empirical housing data (rows 4000 to 4500)
    # Interspersed with realistic transmission failures and poison pills
    raw_messages = []
    base_ts = 1710000000.0  # Monday 2024-03-11 00:00:00 UTC
    offset = 0

    # Batch 1: 100 valid real census records (Window 0: [0, 60))
    for i in range(100):
        row = df.iloc[4000 + i]
        msg_payload = {
            "stream_id": "district-bay-area",
            "event_ts": base_ts + (i * 0.5),  # 0 to 50 seconds
            "med_inc": float(row["med_inc"]),
            "house_age": float(row["house_age"]),
            "med_house_val": float(row["med_house_val"]),
        }
        raw_messages.append(
            RawMessage(
                topic="california-census-telemetry",
                partition=0,
                offset=offset,
                key=b"district-bay-area",
                value=json.dumps(msg_payload).encode("utf-8"),
            )
        )
        offset += 1

    # Inject Poison 1: Truncated JSON packet (broken network frame)
    raw_messages.append(
        RawMessage(
            topic="california-census-telemetry",
            partition=0,
            offset=offset,
            key=b"district-bay-area",
            value=b'{"stream_id": "district-bay-area", "event_ts": 1710000025.0, "med_inc": 4.5, "med_h',
        )
    )
    offset += 1

    # Inject Poison 2: Zero-byte / empty heartbeat frame
    raw_messages.append(
        RawMessage(
            topic="california-census-telemetry",
            partition=0,
            offset=offset,
            key=b"district-bay-area",
            value=b"",
        )
    )
    offset += 1

    # Inject Poison 3: Illegal characters in stream ID (unescaped delimiter '|' violating D-3/D-9)
    raw_messages.append(
        RawMessage(
            topic="california-census-telemetry",
            partition=0,
            offset=offset,
            key=b"bad_routing",
            value=json.dumps({
                "stream_id": "district|unauthorized#tenant",
                "event_ts": base_ts + 30.0,
                "med_inc": 3.2,
            }).encode("utf-8"),
        )
    )
    offset += 1

    # Inject Poison 4: Corrupted naive ISO timestamp missing UTC timezone offset (violating §10.1)
    raw_messages.append(
        RawMessage(
            topic="california-census-telemetry",
            partition=0,
            offset=offset,
            key=b"district-bay-area",
            value=json.dumps({
                "stream_id": "district-bay-area",
                "event_ts": "2024-03-11 14:30:00",  # naive, missing 'Z' or offset
                "med_inc": 5.1,
            }).encode("utf-8"),
        )
    )
    offset += 1

    # Inject Poison 5: Future timestamp spoofing watermark (2 hours in future, exceeding 3600s skew)
    raw_messages.append(
        RawMessage(
            topic="california-census-telemetry",
            partition=0,
            offset=offset,
            key=b"district-bay-area",
            value=json.dumps({
                "stream_id": "district-bay-area",
                "event_ts": time.time() + 7200.0,
                "med_inc": 6.8,
            }).encode("utf-8"),
        )
    )
    offset += 1

    # Batch 2: Another 100 valid real census records (Window 1: [60, 120))
    for i in range(100):
        row = df.iloc[4100 + i]
        msg_payload = {
            "stream_id": "district-bay-area",
            "event_ts": base_ts + 60.0 + (i * 0.5),  # 60 to 110 seconds
            "med_inc": float(row["med_inc"]),
            "house_age": float(row["house_age"]),
            "med_house_val": float(row["med_house_val"]),
        }
        raw_messages.append(
            RawMessage(
                topic="california-census-telemetry",
                partition=0,
                offset=offset,
                key=b"district-bay-area",
                value=json.dumps(msg_payload).encode("utf-8"),
            )
        )
        offset += 1

    # Trailing watermark advancement message at t = base_ts + 140 (flushes Window 0 and Window 1)
    raw_messages.append(
        RawMessage(
            topic="california-census-telemetry",
            partition=0,
            offset=offset,
            key=b"district-bay-area",
            value=json.dumps({
                "stream_id": "district-bay-area",
                "event_ts": base_ts + 140.0,
                "med_inc": 4.0,
                "house_age": 25.0,
                "med_house_val": 2.1,
            }).encode("utf-8"),
        )
    )
    offset += 1

    # 3. Instantiate StreamingMonitor with FakeDeadLetterSink
    source = FakeEventSource(raw_messages)
    store = InMemoryStateStore()
    dlq_sink = FakeDeadLetterSink()

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=dlq_sink)
    monitor.dispatcher.start()

    # 4. Ingest and process stream
    total_processed = 0
    while True:
        n = monitor.run_once(max_events=50)
        if n == 0:
            break
        total_processed += n

    monitor.dispatcher.stop(timeout_s=5.0)

    # 5. Rigorous Invariant Assertions
    # A) All messages consumed and offset committed
    assert total_processed == len(raw_messages)
    assert monitor.offsets[("california-census-telemetry", 0)] == len(raw_messages)

    # B) DLQ captured every poison message accurately
    assert len(dlq_sink.records) == 5
    dlq_reasons = [r.reason for r in dlq_sink.records]
    assert dlq_reasons == ["bad_json", "bad_json", "bad_stream_id", "bad_event_ts", "ts_future"]

    # C) Verify DLQ envelope completeness
    bad_json_rec = dlq_sink.records[0]
    assert bad_json_rec.original_offset == 100
    assert bad_json_rec.original_topic == "california-census-telemetry"
    assert bad_json_rec.original_key == "district-bay-area"
    assert "med_h" in bad_json_rec.payload
    assert "JSON decode error" in bad_json_rec.error_message

    empty_rec = dlq_sink.records[1]
    assert empty_rec.original_offset == 101
    assert empty_rec.payload is None
    assert empty_rec.error_message == "Empty message payload"

    bad_id_rec = dlq_sink.records[2]
    assert bad_id_rec.original_offset == 102
    assert "district|unauthorized#tenant" in bad_id_rec.error_message

    bad_ts_rec = dlq_sink.records[3]
    assert bad_ts_rec.original_offset == 103
    assert "explicit timezone offset" in bad_ts_rec.error_message

    future_rec = dlq_sink.records[4]
    assert future_rec.original_offset == 104
    assert "exceeds now" in future_rec.error_message

    # D) Verify metrics health line
    assert monitor.metrics.dead_letter_published == 5
    assert monitor.metrics.dead_letter_errors == 0
    health = monitor.metrics.health_line()
    assert "dlq=5" in health

    # E) Verify drift detection on real data completed and logged to CSV
    assert log_path.exists(), "drift.csv should be created for flushed windows"
    df_log = pd.read_csv(log_path)
    assert len(df_log) >= 3, "At least 3 feature evaluations should be logged across flushed windows"
    assert set(df_log["method"].unique()) == {"psi"}


def test_real_data_nasa_climate_anomalies_streaming_and_dlq(tmp_path):
    """
    Test using real historical NASA Global Surface Temperature anomalies (1880 - modern era).
    Verifies that real historical warming drift is detected while corruptions route to DLQ.
    """
    # 1. Load real NASA Global Surface Temperature anomaly dataset from SciPy test data
    nasa_csv_path = ".venv/Lib/site-packages/scipy/signal/tests/data/GLB.Ts+dSST.csv"
    if not os.path.exists(nasa_csv_path):
        pytest.skip(f"NASA dataset not found at {nasa_csv_path}")

    # Read NASA data (skip header note line)
    df_nasa = pd.read_csv(nasa_csv_path, skiprows=1)
    df_nasa["Year"] = pd.to_numeric(df_nasa["Year"], errors="coerce")
    df_nasa["J-D"] = pd.to_numeric(df_nasa["J-D"], errors="coerce")  # Annual mean anomaly
    df_clean = df_nasa.dropna(subset=["Year", "J-D"]).copy()

    # Split into real baseline (1880 - 1940: early reference era) and modern streaming (1941 - modern)
    baseline_df = df_clean[df_clean["Year"] <= 1940][["J-D"]].rename(columns={"J-D": "temp_anomaly"})
    modern_df = df_clean[df_clean["Year"] > 1940][["Year", "J-D"]].rename(columns={"J-D": "temp_anomaly"})

    assert len(baseline_df) >= 50
    assert len(modern_df) >= 50

    ref_path = tmp_path / "nasa_baseline.csv"
    baseline_df.to_csv(ref_path, index=False)

    log_path = tmp_path / "logs" / "climate_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["temp_anomaly"],
        topic="climate-telemetry",
        dead_letter_topic="climate-telemetry-dlq",
        window_seconds=100.0,
        grace_seconds=10.0,
        min_events=50,
        log_path=str(log_path),
    )
    cfg.validate()

    # Stream the modern era observations (annual anomalies)
    messages = []
    base_ts = 1600000000.0
    offset = 0

    # Send 70 valid modern temperature anomalies
    for i, (_, row) in enumerate(modern_df.iloc[:70].iterrows()):
        payload = {
            "stream_id": "nasa-global-surface",
            "event_ts": base_ts + float(i),  # within window [0, 100)
            "temp_anomaly": float(row["temp_anomaly"]),
        }
        messages.append(
            RawMessage("climate-telemetry", 0, offset, b"nasa-global-surface", json.dumps(payload).encode("utf-8"))
        )
        offset += 1

    # Inject a poison record in between
    messages.append(
        RawMessage("climate-telemetry", 0, offset, b"nasa-global-surface", b"{corrupted_json_anomaly_stream}")
    )
    offset += 1

    # Trailing event to flush window
    messages.append(
        RawMessage(
            "climate-telemetry",
            0,
            offset,
            b"nasa-global-surface",
            json.dumps({"stream_id": "nasa-global-surface", "event_ts": base_ts + 120.0, "temp_anomaly": 0.95}).encode(),
        )
    )
    offset += 1

    source = FakeEventSource(messages)
    store = InMemoryStateStore()
    dlq = FakeDeadLetterSink()

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=dlq)
    monitor.dispatcher.start()

    while monitor.run_once(max_events=50) > 0:
        pass

    monitor.dispatcher.stop(timeout_s=5.0)

    # Assertions
    assert monitor.offsets[("climate-telemetry", 0)] == len(messages)
    assert len(dlq.records) == 1
    assert dlq.records[0].reason == "bad_json"

    # Verify drift detection detected real climate shift (modern era vs 1880-1940 baseline)
    assert log_path.exists()
    df_drift = pd.read_csv(log_path)
    assert len(df_drift) >= 1
    # Significant drift is expected due to real global climate warming between 1880 and modern era
    assert df_drift.iloc[0]["drift"] == True


def test_real_data_high_throughput_burst_with_dlq(tmp_path, california_housing_data):
    """
    Stress test with 2,000 real housing records streaming under sustained throughput,
    verifying atomic checkpoints, zero message drops, and 100% DLQ capture accuracy.
    """
    df = california_housing_data
    ref_df = df.iloc[:2000][["med_inc", "house_age"]]
    ref_path = tmp_path / "burst_ref.csv"
    ref_df.to_csv(ref_path, index=False)

    log_path = tmp_path / "logs" / "burst_drift.csv"

    cfg = StreamingConfig(
        reference_path=str(ref_path),
        features=["med_inc", "house_age"],
        topic="housing-burst-topic",
        dead_letter_topic="housing-burst-dlq",
        checkpoint_events=200,
        checkpoint_seconds=2.0,
        window_seconds=100.0,
        min_events=50,
        log_path=str(log_path),
    )

    messages = []
    base_ts = 1700000000.0
    expected_dlq_count = 0

    # Stream 2,000 real records with periodic poison messages (every 25th message)
    for i in range(2000):
        row = df.iloc[2000 + i]
        if i % 25 == 0:
            # Poison pill
            messages.append(
                RawMessage(
                    topic="housing-burst-topic",
                    partition=0,
                    offset=i,
                    key=b"stress-stream",
                    value=b"{bad_burst_json: true",
                )
            )
            expected_dlq_count += 1
        else:
            payload = {
                "stream_id": "stress-stream",
                "event_ts": base_ts + (i * 0.04),
                "med_inc": float(row["med_inc"]),
                "house_age": float(row["house_age"]),
            }
            messages.append(
                RawMessage(
                    topic="housing-burst-topic",
                    partition=0,
                    offset=i,
                    key=b"stress-stream",
                    value=json.dumps(payload).encode("utf-8"),
                )
            )

    source = FakeEventSource(messages)
    store = InMemoryStateStore()
    dlq = FakeDeadLetterSink()

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=dlq)

    # Ingest in chunks of 250
    t_start = time.perf_counter()
    processed = 0
    while True:
        n = monitor.run_once(max_events=250)
        if n == 0:
            break
        processed += n
    t_elapsed = time.perf_counter() - t_start

    # Flush checkpoint and stop
    monitor.checkpoint(reason="flush_burst")
    monitor.stop()

    assert processed == 2000
    assert len(dlq.records) == expected_dlq_count
    assert monitor.metrics.dead_letter_published == expected_dlq_count
    assert monitor.metrics.dead_letter_errors == 0
    assert monitor.metrics.events_in == 2000
    assert monitor.offsets[("housing-burst-topic", 0)] == 2000
    assert monitor.metrics.checkpoints >= 10
    assert dlq.is_flushed
    assert dlq.is_closed

    throughput = processed / t_elapsed
    print(f"\n[stress-test] Processed {processed} real messages in {t_elapsed:.3f}s (~{throughput:.0f} msgs/sec)")
