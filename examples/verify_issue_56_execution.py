"""
Execution verification script for Issue #56:
Dead-letter topic for rejected streaming messages.
Demonstrates live execution of StreamingMonitor processing valid telemetry
while routing rejected/poison messages to the DLQ sink.
"""

import json
import time
import os
import pandas as pd
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor
from etsi.watchdog.streaming.dlq import FakeDeadLetterSink


def main():
    print("=" * 70)
    print("EXECUTION DEMO: Issue #56 - Dead-Letter Topic for Streaming Rejects")
    print("=" * 70)

    # 1. Setup sample reference dataset
    os.makedirs("logs", exist_ok=True)
    ref_path = "logs/ref_demo.csv"
    pd.DataFrame({"metric": [10.0, 11.0, 12.0, 10.5, 11.5, 12.5] * 20}).to_csv(ref_path, index=False)

    # 2. Configure Streaming Monitor with Dead-Letter Topic enabled
    cfg = StreamingConfig(
        reference_path=ref_path,
        features=["metric"],
        topic="telemetry-in",
        dead_letter_topic="telemetry-dlq",
        window_seconds=10.0,
        grace_seconds=5.0,
        min_events=5,
        max_streams=2,
        log_path="logs/drift_demo.csv",
    )
    cfg.validate()
    print(f"[config] Topic: {cfg.topic}")
    print(f"[config] Dead-Letter Topic: {cfg.dead_letter_topic}")

    now = time.time()
    # 3. Stream containing valid messages and multiple poison message variants
    messages = [
        # Normal event 1
        RawMessage("telemetry-in", 0, 1, b"s1", json.dumps({"stream_id": "s1", "event_ts": now, "metric": 11.0}).encode()),
        # Poison 1: Bad JSON syntax
        RawMessage("telemetry-in", 0, 2, b"s1", b"{malformed json: true,"),
        # Poison 2: Empty payload
        RawMessage("telemetry-in", 0, 3, b"s1", b""),
        # Poison 3: Invalid stream_id characters
        RawMessage("telemetry-in", 0, 4, b"bad_id", json.dumps({"stream_id": "s1|bad", "event_ts": now, "metric": 10.0}).encode()),
        # Poison 4: Invalid timestamp string
        RawMessage("telemetry-in", 0, 5, b"s1", json.dumps({"stream_id": "s1", "event_ts": "bad-date-time", "metric": 10.0}).encode()),
        # Poison 5: Future timestamp exceeding watermark skew limit
        RawMessage("telemetry-in", 0, 6, b"s1", json.dumps({"stream_id": "s1", "event_ts": now + 7200.0, "metric": 10.0}).encode()),
        # Normal event 2
        RawMessage("telemetry-in", 0, 7, b"s2", json.dumps({"stream_id": "s2", "event_ts": now, "metric": 12.0}).encode()),
        # Poison 6: Stream capacity exceeded (max_streams=2, this is stream 3)
        RawMessage("telemetry-in", 0, 8, b"s3", json.dumps({"stream_id": "s3", "event_ts": now, "metric": 10.0}).encode()),
    ]

    source = FakeEventSource(messages)
    store = InMemoryStateStore()
    dlq_sink = FakeDeadLetterSink()

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=dlq_sink)
    monitor.dispatcher.start()

    print("\n[processing] Ingesting batch through StreamingMonitor...")
    count = monitor.run_once(max_events=10)
    print(f"[processing] Processed {count} messages from source.")

    monitor.dispatcher.stop(timeout_s=2.0)

    # 4. Inspect Results
    print("\n--- Health Line ---")
    print(monitor.metrics.health_line())

    print("\n--- DLQ Published Rejects ---")
    print(f"Total Published to DLQ: {len(dlq_sink.records)}")
    for idx, rec in enumerate(dlq_sink.records, 1):
        print(f"  {idx}. [Reason: {rec.reason}] [Offset: {rec.original_offset}] [Key: {rec.original_key}] Error: {rec.error_message}")

    print("\n--- Partition Offset Progress ---")
    print(f"Committed Offset for telemetry-in/0: {monitor.offsets[('telemetry-in', 0)]}")
    assert monitor.offsets[("telemetry-in", 0)] == 9, "Offset must have advanced past all 8 messages!"

    print("\n[✓] Issue #56 execution verified successfully!")
    print("=" * 70)


if __name__ == "__main__":
    main()
