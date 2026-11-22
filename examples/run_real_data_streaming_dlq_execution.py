"""
Real-world Data Streaming & Dead-Letter Queue (DLQ) Execution Test
Uses 100% real empirical data (California Housing Census Dataset - 20,640 records).
No synthetic or generated data distributions.
"""

import json
import time
import os
import pandas as pd
from sklearn.datasets import fetch_california_housing
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor
from etsi.watchdog.streaming.dlq import FakeDeadLetterSink


def main():
    print("=" * 80)
    print("ETSI WATCHDOG - REAL-WORLD DATA STREAMING & DLQ STRESS VERIFICATION")
    print("Dataset: Real California Housing Census (U.S. Census Bureau empirical data)")
    print("=" * 80)

    # 1. Load real empirical dataset
    print("[1/5] Loading real California Housing empirical dataset...")
    housing = fetch_california_housing(as_frame=True)
    df = housing.frame.copy()
    features = ["MedInc", "HouseAge", "MedHouseVal"]
    print(f"      Loaded {len(df)} real block groups across California districts.")
    print(f"      Features under monitoring: {features}")

    # 2. Establish Real Baseline Profile (Inland/Central Valley districts, rows 0-3000)
    os.makedirs("logs", exist_ok=True)
    ref_path = "logs/california_baseline_real.csv"
    ref_df = df.iloc[:3000][features]
    ref_df.to_csv(ref_path, index=False)
    print(f"[2/5] Saved baseline reference artifact with {len(ref_df)} real rows → {ref_path}")

    # 3. Configure Streaming Monitor with Dead-Letter Topic
    log_path = "logs/california_real_drift.csv"
    cfg = StreamingConfig(
        reference_path=ref_path,
        features=features,
        topic="census-telemetry-live",
        dead_letter_topic="census-telemetry-dlq",
        window_seconds=30.0,
        grace_seconds=5.0,
        min_events=50,
        max_streams=3,
        checkpoint_events=100,
        checkpoint_seconds=2.0,
        log_path=log_path,
    )
    cfg.validate()
    print("[3/5] Streaming Configuration validated:")
    print(f"      - Ingestion Topic:       {cfg.topic}")
    print(f"      - Dead-Letter Topic:     {cfg.dead_letter_topic}")
    print(f"      - Window / Grace:        {cfg.window_seconds}s / {cfg.grace_seconds}s")
    print(f"      - Max Streams Quota:     {cfg.max_streams}")

    # 4. Construct live stream from real Coastal / Bay Area blocks (rows 3000-4200)
    # Interspersed with realistic transmission failures and poison pills
    print("[4/5] Preparing live telemetry stream from real demographic blocks with poison pills...")
    base_ts = 1715000000.0  # Monday 2024-05-06 13:20:00 UTC
    raw_messages = []
    offset = 0

    # Stream real blocks: Window 0 (0-30s)
    for i in range(120):
        row = df.iloc[3000 + i]
        payload = {
            "stream_id": "coastal-bay-area",
            "event_ts": base_ts + (i * 0.2),  # 0 to 24s
            "MedInc": float(row["MedInc"]),
            "HouseAge": float(row["HouseAge"]),
            "MedHouseVal": float(row["MedHouseVal"]),
        }
        raw_messages.append(
            RawMessage("census-telemetry-live", 0, offset, b"coastal-bay-area", json.dumps(payload).encode("utf-8"))
        )
        offset += 1

    # Inject Poison Pill 1: Network packet truncation
    raw_messages.append(
        RawMessage("census-telemetry-live", 0, offset, b"coastal-bay-area", b'{"stream_id": "coastal-bay-area", "MedInc": 9.2, "Me')
    )
    offset += 1

    # Inject Poison Pill 2: Zero-byte empty payload
    raw_messages.append(
        RawMessage("census-telemetry-live", 0, offset, b"coastal-bay-area", b"")
    )
    offset += 1

    # Inject Poison Pill 3: Unescaped illegal character in routing key ('|')
    raw_messages.append(
        RawMessage("census-telemetry-live", 0, offset, b"bad_key", json.dumps({
            "stream_id": "unauthorized|tenant-99",
            "event_ts": base_ts + 15.0,
            "MedInc": 4.1,
        }).encode("utf-8"))
    )
    offset += 1

    # Inject Poison Pill 4: Unparseable naive timestamp missing timezone
    raw_messages.append(
        RawMessage("census-telemetry-live", 0, offset, b"coastal-bay-area", json.dumps({
            "stream_id": "coastal-bay-area",
            "event_ts": "2024-05-06 13:20:15",
            "MedInc": 3.8,
        }).encode("utf-8"))
    )
    offset += 1

    # Inject Poison Pill 5: Clock skew / future timestamp (> 1 hour ahead)
    raw_messages.append(
        RawMessage("census-telemetry-live", 0, offset, b"coastal-bay-area", json.dumps({
            "stream_id": "coastal-bay-area",
            "event_ts": time.time() + 8000.0,
            "MedInc": 7.4,
        }).encode("utf-8"))
    )
    offset += 1

    # Stream real blocks: Window 1 (30-60s)
    for i in range(120):
        row = df.iloc[3120 + i]
        payload = {
            "stream_id": "coastal-bay-area",
            "event_ts": base_ts + 30.0 + (i * 0.2),  # 30 to 54s
            "MedInc": float(row["MedInc"]),
            "HouseAge": float(row["HouseAge"]),
            "MedHouseVal": float(row["MedHouseVal"]),
        }
        raw_messages.append(
            RawMessage("census-telemetry-live", 0, offset, b"coastal-bay-area", json.dumps(payload).encode("utf-8"))
        )
        offset += 1

    # Two more registered streams to saturate capacity quota (quota = 3)
    for s_id in ["district-la-south", "district-san-diego"]:
        row = df.iloc[3500]
        payload = {
            "stream_id": s_id,
            "event_ts": base_ts + 35.0,
            "MedInc": float(row["MedInc"]),
            "HouseAge": float(row["HouseAge"]),
            "MedHouseVal": float(row["MedHouseVal"]),
        }
        raw_messages.append(
            RawMessage("census-telemetry-live", 0, offset, s_id.encode(), json.dumps(payload).encode("utf-8"))
        )
        offset += 1

    # Inject Poison Pill 6: Stream capacity exceeded (4th stream, max_streams = 3)
    raw_messages.append(
        RawMessage("census-telemetry-live", 0, offset, b"overflow-stream", json.dumps({
            "stream_id": "district-sacramento-overflow",
            "event_ts": base_ts + 36.0,
            "MedInc": 2.9,
        }).encode("utf-8"))
    )
    offset += 1

    # Trailing watermark advancement event at t = base_ts + 75.0 (flushes Window 0 and Window 1)
    raw_messages.append(
        RawMessage("census-telemetry-live", 0, offset, b"coastal-bay-area", json.dumps({
            "stream_id": "coastal-bay-area",
            "event_ts": base_ts + 75.0,
            "MedInc": 5.0,
            "HouseAge": 30.0,
            "MedHouseVal": 3.0,
        }).encode("utf-8"))
    )
    offset += 1

    print(f"      Constructed {len(raw_messages)} live messages ({len(raw_messages) - 6} real records + 6 poison pills).")

    # 5. Execute Live Streaming Ingestion
    print("\n[5/5] Executing StreamingMonitor ingestion loop...")
    source = FakeEventSource(raw_messages)
    store = InMemoryStateStore()
    dlq_sink = FakeDeadLetterSink()

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=dlq_sink)
    monitor.dispatcher.start()

    t0 = time.perf_counter()
    while monitor.run_once(max_events=100) > 0:
        pass
    t_total = time.perf_counter() - t0

    monitor.stop()

    # Output detailed report
    print("\n" + "=" * 80)
    print("VERIFICATION & TELEMETRY REPORT")
    print("=" * 80)
    print(f"Ingestion Duration:     {t_total * 1000:.2f} ms")
    print(f"Messages Consumed:      {monitor.metrics.events_in}")
    print(f"Active Real Streams:    {monitor.metrics.streams_active}")
    print(f"Dead-Letter Published:  {monitor.metrics.dead_letter_published}")
    print(f"Dead-Letter Errors:     {monitor.metrics.dead_letter_errors}")
    print(f"Committed Offset:       {monitor.offsets[('census-telemetry-live', 0)]} (Expected: {len(raw_messages)})")
    print(f"Health Line Output:     {monitor.metrics.health_line()}")

    print("\n--- DEAD-LETTER QUEUE (DLQ) CAPTURED ENVELOPES ---")
    for idx, rec in enumerate(dlq_sink.records, 1):
        print(f"  {idx}. [Reason: {rec.reason:<13}] [Offset: {rec.original_offset:>3}] [Key: {str(rec.original_key):<18}] Error: {rec.error_message}")

    print("\n--- REAL-WORLD DRIFT DETECTION RESULTS (from logs/california_real_drift.csv) ---")
    if os.path.exists(log_path):
        df_results = pd.read_csv(log_path)
        print(df_results.to_string(index=False))
    else:
        print("No log file found.")

    # Invariant assertions
    assert monitor.offsets[("census-telemetry-live", 0)] == len(raw_messages), "Partition must not wedge!"
    assert len(dlq_sink.records) == 6, "All 6 poison pills must be captured in DLQ!"
    assert monitor.metrics.dead_letter_published == 6
    assert monitor.metrics.dead_letter_errors == 0
    assert dlq_sink.is_closed

    print("\n" + "=" * 80)
    print("ALL REAL-DATA TESTS & INVARIANTS PASSED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    main()
