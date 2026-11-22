# tests/streaming/test_dead_letter_queue.py

import json
import time
import pytest
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor
from etsi.watchdog.streaming.dlq import (
    DeadLetterRecord,
    FakeDeadLetterSink,
    KafkaDeadLetterSink,
)


def test_dead_letter_record_serialization():
    """Verify DeadLetterRecord serialization to dict, JSON, and bytes."""
    rec = DeadLetterRecord(
        reason="bad_json",
        error_message="Invalid JSON syntax",
        timestamp=1700000000.0,
        original_topic="telemetry",
        original_partition=1,
        original_offset=42,
        original_key="sensor-1",
        payload="{bad_json}",
    )
    d = rec.to_dict()
    assert d["reason"] == "bad_json"
    assert d["original_offset"] == 42
    assert d["payload"] == "{bad_json}"

    raw_json = rec.to_json()
    parsed = json.loads(raw_json)
    assert parsed["reason"] == "bad_json"
    assert parsed["original_key"] == "sensor-1"

    raw_bytes = rec.to_bytes()
    assert isinstance(raw_bytes, bytes)
    assert json.loads(raw_bytes.decode("utf-8"))["error_message"] == "Invalid JSON syntax"


def test_dead_letter_config_and_validation(monkeypatch):
    """Verify WATCHDOG_DEAD_LETTER_TOPIC and WATCHDOG_DLQ_TOPIC env loading and validation."""
    monkeypatch.setenv("WATCHDOG_REFERENCE_PATH", "ref.csv")
    monkeypatch.setenv("WATCHDOG_FEATURES", "temp")
    monkeypatch.setenv("WATCHDOG_TOPIC", "in-topic")

    # 1. Default: dead_letter_topic is None
    monkeypatch.delenv("WATCHDOG_DEAD_LETTER_TOPIC", raising=False)
    monkeypatch.delenv("WATCHDOG_DLQ_TOPIC", raising=False)
    cfg1 = StreamingConfig.from_env()
    assert cfg1.dead_letter_topic is None

    # 2. WATCHDOG_DEAD_LETTER_TOPIC set
    monkeypatch.setenv("WATCHDOG_DEAD_LETTER_TOPIC", "telemetry-dlq")
    cfg2 = StreamingConfig.from_env()
    assert cfg2.dead_letter_topic == "telemetry-dlq"

    # 3. WATCHDOG_DLQ_TOPIC fallback
    monkeypatch.delenv("WATCHDOG_DEAD_LETTER_TOPIC", raising=False)
    monkeypatch.setenv("WATCHDOG_DLQ_TOPIC", "fallback-dlq")
    cfg3 = StreamingConfig.from_env()
    assert cfg3.dead_letter_topic == "fallback-dlq"

    # 4. Empty/whitespace topic fails validation
    cfg_invalid = StreamingConfig(
        reference_path="ref.csv",
        features=["temp"],
        topic="in-topic",
        dead_letter_topic="   ",
    )
    with pytest.raises(ValueError, match="WATCHDOG_DEAD_LETTER_TOPIC cannot be empty"):
        cfg_invalid.validate()


def test_streaming_monitor_without_dlq_preserves_v1_behavior(tmp_path):
    """When DLQ is not configured, bad messages are dropped and counted without error."""
    ref_file = tmp_path / "reference.csv"
    ref_file.write_text("feature_a\n1.0\n2.0\n3.0\n")

    cfg = StreamingConfig(
        reference_path=str(ref_file),
        features=["feature_a"],
        topic="telemetry",
        log_path=str(tmp_path / "logs" / "drift.csv"),
    )

    bad_msgs = [
        RawMessage(topic="telemetry", partition=0, offset=0, key=b"k1", value=b"invalid-json"),
        RawMessage(topic="telemetry", partition=0, offset=1, key=b"k2", value=b""),
    ]
    source = FakeEventSource(bad_msgs)
    store = InMemoryStateStore()
    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=None)

    processed = monitor.run_once()
    assert processed == 2
    assert monitor.metrics.events_rejected["bad_json"] == 2
    assert monitor.metrics.dead_letter_published == 0
    assert monitor.offsets[("telemetry", 0)] == 2


def test_streaming_monitor_with_dlq_publishes_all_reject_reasons(tmp_path):
    """
    Issue #56:
    When DLQ is enabled, all poison / rejected messages are published to DLQ
    with reason, error diagnostic, and message coordinates.
    """
    ref_file = tmp_path / "reference.csv"
    ref_file.write_text("temp\n20.0\n21.0\n22.0\n")

    cfg = StreamingConfig(
        reference_path=str(ref_file),
        features=["temp"],
        topic="telemetry",
        max_streams=2,
        max_future_skew_seconds=3600.0,
        log_path=str(tmp_path / "logs" / "drift.csv"),
    )

    now = time.time()
    poison_messages = [
        # 1. bad_json (unparseable)
        RawMessage(topic="telemetry", partition=0, offset=100, key=b"s1", value=b"NOT_VALID_JSON{"),
        # 2. bad_json (empty payload)
        RawMessage(topic="telemetry", partition=0, offset=101, key=b"s1", value=b""),
        # 3. bad_stream_id (contains illegal character '|')
        RawMessage(
            topic="telemetry",
            partition=0,
            offset=102,
            key=b"bad_id",
            value=json.dumps({"stream_id": "stream|bad", "event_ts": now, "temp": 20.0}).encode(),
        ),
        # 4. bad_event_ts (unparseable string timestamp)
        RawMessage(
            topic="telemetry",
            partition=0,
            offset=103,
            key=b"s1",
            value=json.dumps({"stream_id": "s1", "event_ts": "not-a-timestamp", "temp": 20.0}).encode(),
        ),
        # 5. ts_future (far future timestamp exceeding max_future_skew)
        RawMessage(
            topic="telemetry",
            partition=0,
            offset=104,
            key=b"s1",
            value=json.dumps({"stream_id": "s1", "event_ts": now + 7200.0, "temp": 20.0}).encode(),
        ),
        # 6. Valid event for stream-1 (stream count = 1)
        RawMessage(
            topic="telemetry",
            partition=0,
            offset=105,
            key=b"stream-1",
            value=json.dumps({"stream_id": "stream-1", "event_ts": now, "temp": 20.5}).encode(),
        ),
        # 7. Valid event for stream-2 (stream count = 2 = max_streams)
        RawMessage(
            topic="telemetry",
            partition=0,
            offset=106,
            key=b"stream-2",
            value=json.dumps({"stream_id": "stream-2", "event_ts": now, "temp": 21.0}).encode(),
        ),
        # 8. stream_cap exceeded for stream-3 (max_streams is 2)
        RawMessage(
            topic="telemetry",
            partition=0,
            offset=107,
            key=b"stream-3",
            value=json.dumps({"stream_id": "stream-3", "event_ts": now, "temp": 22.0}).encode(),
        ),
    ]

    source = FakeEventSource(poison_messages)
    store = InMemoryStateStore()
    dlq_sink = FakeDeadLetterSink()

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=dlq_sink)

    processed = monitor.run_once(max_events=10)
    assert processed == 8

    # Verify metrics counters
    assert monitor.metrics.events_in == 8
    assert monitor.metrics.events_rejected["bad_json"] == 2
    assert monitor.metrics.events_rejected["bad_stream_id"] == 1
    assert monitor.metrics.events_rejected["bad_event_ts"] == 1
    assert monitor.metrics.events_rejected["ts_future"] == 1
    assert monitor.metrics.events_rejected["stream_cap"] == 1
    assert monitor.metrics.dead_letter_published == 6
    assert monitor.metrics.dead_letter_errors == 0

    # Offset must advance through all 8 messages
    assert monitor.offsets[("telemetry", 0)] == 108

    # Verify DLQ records
    assert len(dlq_sink.records) == 6
    reasons = [r.reason for r in dlq_sink.records]
    assert reasons == [
        "bad_json",
        "bad_json",
        "bad_stream_id",
        "bad_event_ts",
        "ts_future",
        "stream_cap",
    ]

    # Verify coordinates of message 100
    r0 = dlq_sink.records[0]
    assert r0.original_topic == "telemetry"
    assert r0.original_partition == 0
    assert r0.original_offset == 100
    assert r0.original_key == "s1"
    assert r0.payload == "NOT_VALID_JSON{"
    assert "JSON decode error" in r0.error_message

    # Verify empty message
    r1 = dlq_sink.records[1]
    assert r1.original_offset == 101
    assert r1.payload is None
    assert r1.error_message == "Empty message payload"

    # Verify health line output includes DLQ metric
    health = monitor.metrics.health_line()
    assert "dlq=6" in health


def test_streaming_monitor_dlq_publisher_failure_resilience(tmp_path):
    """
    DLQ publisher failure must not crash the monitor or stall the partition.
    Offset must advance and errors must be recorded.
    """
    ref_file = tmp_path / "reference.csv"
    ref_file.write_text("feature_a\n1.0\n2.0\n")

    cfg = StreamingConfig(
        reference_path=str(ref_file),
        features=["feature_a"],
        topic="telemetry",
        log_path=str(tmp_path / "logs" / "drift.csv"),
    )

    bad_msgs = [
        RawMessage(topic="telemetry", partition=0, offset=10, key=b"k1", value=b"invalid"),
    ]
    source = FakeEventSource(bad_msgs)
    store = InMemoryStateStore()
    failing_dlq = FakeDeadLetterSink(simulate_failure=True)

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=failing_dlq)

    # Must process without throwing
    processed = monitor.run_once()
    assert processed == 1
    assert monitor.metrics.dead_letter_published == 0
    assert monitor.metrics.dead_letter_errors == 1
    assert monitor.offsets[("telemetry", 0)] == 11


def test_streaming_monitor_dlq_flushed_on_checkpoint_and_closed_on_stop(tmp_path):
    """Verify DLQ sink flush on checkpoint and close on shutdown."""
    ref_file = tmp_path / "reference.csv"
    ref_file.write_text("feature_a\n1.0\n2.0\n")

    cfg = StreamingConfig(
        reference_path=str(ref_file),
        features=["feature_a"],
        topic="telemetry",
        log_path=str(tmp_path / "logs" / "drift.csv"),
    )

    source = FakeEventSource([])
    store = InMemoryStateStore()
    dlq_sink = FakeDeadLetterSink()

    monitor = StreamingMonitor(config=cfg, source=source, store=store, dlq_sink=dlq_sink)
    assert not dlq_sink.is_flushed
    assert not dlq_sink.is_closed

    # Trigger checkpoint -> flushes DLQ
    monitor.checkpoint(reason="manual")
    assert dlq_sink.is_flushed

    # Stop -> closes DLQ
    monitor.stop()
    assert dlq_sink.is_closed
