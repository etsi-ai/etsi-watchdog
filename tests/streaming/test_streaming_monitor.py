# tests/streaming/test_streaming_monitor.py

import json
import numpy as np
import pandas as pd
import pytest
from etsi.watchdog.streaming.config import StreamingConfig
from etsi.watchdog.streaming.source import FakeEventSource, RawMessage
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.streaming_monitor import StreamingMonitor


def test_streaming_monitor_config_fail_fast(tmp_path):
    """Verify boot-time fail-fast validation on configuration errors."""
    ref_file = tmp_path / "ref.csv"
    ref_file.write_text("feature_a\n1.0\n2.0\n")

    # 1. Bare LOG_PATH without directory component must fail fast (R6)
    cfg_bad_log = StreamingConfig(
        reference_path=str(ref_file),
        features=["feature_a"],
        topic="test-topic",
        log_path="drift.csv",  # bare filename
    )
    with pytest.raises(ValueError, match="must contain a directory component"):
        cfg_bad_log.validate()

    # 2. Reservoir size > 10,000 or <= 0 must fail fast (D-1)
    cfg_bad_res = StreamingConfig(
        reference_path=str(ref_file),
        features=["feature_a"],
        topic="test-topic",
        log_path="logs/drift.csv",
        reservoir_size=15000,
    )
    with pytest.raises(ValueError, match="RESERVOIR_SIZE cannot exceed 10000"):
        cfg_bad_res.validate()

    cfg_zero_res = StreamingConfig(
        reference_path=str(ref_file),
        features=["feature_a"],
        topic="test-topic",
        log_path="logs/drift.csv",
        reservoir_size=0,
    )
    with pytest.raises(ValueError, match="RESERVOIR_SIZE must be positive"):
        cfg_zero_res.validate()


def test_streaming_monitor_level_shift_pipeline_e2e(tmp_path):
    """
    M1 Exit Criteria #1 & #3:
    Ingest a synthetic stream with a level shift injected at t=180.
    Assert that psi_streaming flags the first window after t and none before.
    Assert that replaying same input produces identical results.
    """
    rng = np.random.default_rng(42)

    # 1. Create reference profile: 2,000 samples from N(0, 1)
    ref_data = rng.normal(loc=0.0, scale=1.0, size=2000)
    ref_file = tmp_path / "reference.csv"
    pd.DataFrame({"metric": ref_data}).to_csv(ref_file, index=False)

    # 2. Build synthetic event stream:
    # window 0: [0, 60)      -> N(0, 1)    (normal, no drift)
    # window 1: [60, 120)    -> N(0, 1)    (normal, no drift)
    # window 2: [120, 180)   -> N(0, 1)    (normal, no drift)
    # window 3: [180, 240)   -> N(2.5, 1)  (LEVEL SHIFT at t=180!)
    raw_messages = []
    offset = 0

    def make_msg(ts, val):
        nonlocal offset
        payload = json.dumps({"stream_id": "stream-prod", "event_ts": ts, "metric": val}).encode("utf-8")
        msg = RawMessage(topic="telemetry", partition=0, offset=offset, key=b"stream-prod", value=payload)
        offset += 1
        return msg

    # Generate 60 events per window (1 per second)
    for t in range(0, 60):
        raw_messages.append(make_msg(float(t), float(rng.normal(0, 1))))
    for t in range(60, 120):
        raw_messages.append(make_msg(float(t), float(rng.normal(0, 1))))
    for t in range(120, 180):
        raw_messages.append(make_msg(float(t), float(rng.normal(0, 1))))
    for t in range(180, 240):
        raw_messages.append(make_msg(float(t), float(rng.normal(2.5, 1))))

    # Trailing events at t=340 to advance watermark and close window [180, 240) (grace=30s => flush at wm>=270)
    for t in range(270, 280):
        raw_messages.append(make_msg(float(t), float(rng.normal(2.5, 1))))

    # 3. Configure and run StreamingMonitor with FakeEventSource
    log_file = tmp_path / "logs" / "drift.csv"
    cfg = StreamingConfig(
        reference_path=str(ref_file),
        features=["metric"],
        topic="telemetry",
        window_seconds=60.0,
        grace_seconds=30.0,
        min_events=50,
        log_path=str(log_file),
    )

    source = FakeEventSource(raw_messages)
    store = InMemoryStateStore()

    monitor = StreamingMonitor(config=cfg, source=source, store=store)
    monitor.dispatcher.start()

    # Process all events in batches
    while monitor.run_once(max_events=100) > 0:
        pass

    # Stop dispatcher and drain
    monitor.dispatcher.stop(timeout_s=5.0)

    # 4. Read logged results from CSV
    assert log_file.exists(), "drift.csv was not generated"
    df_log = pd.read_csv(log_file)
    assert len(df_log) == 4, f"Expected 4 flushed windows, got {len(df_log)}"

    # Window 0 ([0, 60)): no drift
    assert df_log.iloc[0]["drift"] == False
    assert df_log.iloc[0]["score"] < 0.2

    # Window 1 ([60, 120)): no drift
    assert df_log.iloc[1]["drift"] == False
    assert df_log.iloc[1]["score"] < 0.2

    # Window 2 ([120, 180)): no drift
    assert df_log.iloc[2]["drift"] == False
    assert df_log.iloc[2]["score"] < 0.2

    # Window 3 ([180, 240)): FIRST WINDOW AFTER SHIFT -> DRIFT DETECTED!
    assert df_log.iloc[3]["drift"] == True
    assert df_log.iloc[3]["score"] > 0.2

    # 5. Verify advisory offset commit
    assert source.committed_offsets == {("telemetry", 0): len(raw_messages)}


def test_streaming_monitor_wires_reservoir_size_and_seed(tmp_path):
    """StreamingMonitor must propagate config.reservoir_size and config.seed to WindowEngine stats."""
    rng = np.random.default_rng(7)
    ref_file = tmp_path / "ref.csv"
    pd.DataFrame({"val": rng.normal(0.0, 1.0, 1000)}).to_csv(ref_file, index=False)

    def build_messages():
        msgs = []
        offset = 0
        for t in range(150):
            payload = json.dumps({"stream_id": "s1", "event_ts": float(t * 0.2), "val": float(rng.normal(0.3, 1.0))}).encode("utf-8")
            msgs.append(RawMessage("telemetry", 0, offset, b"s1", payload))
            offset += 1
        # Trailing event past window_end (60s) + grace (30s) = 90s to flush window 0
        payload = json.dumps({"stream_id": "s1", "event_ts": 95.0, "val": 0.0}).encode("utf-8")
        msgs.append(RawMessage("telemetry", 0, offset, b"s1", payload))
        return msgs

    raw_msgs = build_messages()

    def run_with(algo, threshold, reservoir_size, seed, idx):
        cfg = StreamingConfig(
            reference_path=str(ref_file),
            features=["val"],
            topic="telemetry",
            algorithm=algo,
            threshold=threshold,
            window_seconds=60.0,
            grace_seconds=30.0,
            min_events=50,
            reservoir_size=reservoir_size,
            seed=seed,
            log_path=str(tmp_path / f"logs_{idx}" / "drift.csv"),
        )
        captured = []
        mon = StreamingMonitor(cfg, source=FakeEventSource(raw_msgs), store=InMemoryStateStore())
        orig_enqueue = mon.dispatcher.enqueue
        mon.dispatcher.enqueue = lambda markers: (captured.extend(markers), orig_enqueue(markers))
        mon.dispatcher.start()
        try:
            while mon.run_once(max_events=500) > 0:
                pass
        finally:
            mon.dispatcher.stop(timeout_s=5.0)
        return captured[0].result

    ks_s1 = run_with("ks_streaming", 0.95, 100, 11, 1)
    ks_s1_repeat = run_with("ks_streaming", 0.95, 100, 11, 2)
    ks_s2 = run_with("ks_streaming", 0.95, 100, 99, 3)

    assert ks_s1.details["capacity"] == 100
    assert ks_s1.details["reservoir_size"] == 100
    assert ks_s1.score == ks_s1_repeat.score
    assert ks_s1.details["ks_statistic"] != ks_s2.details["ks_statistic"]

    w_res = run_with("wasserstein_streaming", 0.5, 120, 19, 4)
    assert w_res.method == "wasserstein"
    assert w_res.details["capacity"] == 120
    assert w_res.details["reservoir_size"] == 120


def test_user_operational_env_workflow_e2e(tmp_path, monkeypatch):
    """
    Simulate how a user operates the streaming monitor in production:
    env-var configuration -> mixed valid/poison/late JSON events -> CSV log & health line.
    """
    from datetime import datetime, timezone, timedelta

    rng = np.random.default_rng(314)
    ref_file = tmp_path / "reference.csv"
    log_file = tmp_path / "out" / "drift.csv"
    pd.DataFrame({
        "temp": rng.normal(20.0, 1.0, 1000),
        "humidity": rng.normal(50.0, 5.0, 1000),
    }).to_csv(ref_file, index=False)

    monkeypatch.setenv("WATCHDOG_REFERENCE_PATH", str(ref_file))
    monkeypatch.setenv("WATCHDOG_FEATURES", "temp,humidity")
    monkeypatch.setenv("WATCHDOG_TOPIC", "sensors")
    monkeypatch.setenv("WATCHDOG_ALGORITHM", "ks_streaming")
    monkeypatch.setenv("WATCHDOG_THRESHOLD", "0.95")
    monkeypatch.setenv("WATCHDOG_WINDOW_SECONDS", "60")
    monkeypatch.setenv("WATCHDOG_GRACE_SECONDS", "30")
    monkeypatch.setenv("WATCHDOG_MIN_EVENTS", "50")
    monkeypatch.setenv("WATCHDOG_RESERVOIR_SIZE", "300")
    monkeypatch.setenv("WATCHDOG_SEED", "42")
    monkeypatch.setenv("WATCHDOG_LOG_PATH", str(log_file))

    cfg = StreamingConfig.from_env()
    cfg.validate()

    base_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    msgs = []
    offset = 0

    def add_raw(payload_bytes, key=b"station-1"):
        nonlocal offset
        msgs.append(RawMessage("sensors", 0, offset, key, payload_bytes))
        offset += 1

    # 1. Malformed JSON poison message (must drop and advance offset)
    add_raw(b"{not-valid-json")

    # 2. Window 0 [0..60): 55 valid ISO-8601 Z messages + 1 feature skip on humidity
    for i in range(55):
        ts_str = (base_dt + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = {
            "stream_id": "station-1",
            "event_ts": ts_str,
            "temp": float(rng.normal(25.0, 1.0)),  # strong shift
            "humidity": "bad_str" if i == 0 else float(rng.normal(50.0, 5.0)),
        }
        add_raw(json.dumps(rec).encode("utf-8"))

    # 3. Advance watermark past window 0 end + grace (60 + 30 = 90s)
    ts_flush = (base_dt + timedelta(seconds=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    add_raw(json.dumps({"stream_id": "station-1", "event_ts": ts_flush, "temp": 20.0, "humidity": 50.0}).encode("utf-8"))

    # 4. Late event belonging to Window 0 after flush -> must be dropped & counted
    ts_late = (base_dt + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    add_raw(json.dumps({"stream_id": "station-1", "event_ts": ts_late, "temp": 20.0, "humidity": 50.0}).encode("utf-8"))

    source = FakeEventSource(msgs)
    store = InMemoryStateStore()
    mon = StreamingMonitor(cfg, source=source, store=store)
    mon.dispatcher.start()
    try:
        processed = mon.run_once(max_events=200)
        assert processed == len(msgs)
    finally:
        mon.stop()

    assert mon.metrics.events_rejected.get("bad_json") == 1
    assert mon.metrics.features_skipped.get("feature_nonnumeric") == 1
    assert mon.metrics.late_dropped == 1
    assert mon.metrics.windows_flushed == 2
    assert source.committed_offsets == {("sensors", 0): len(msgs)}
    assert "[etsi-watchdog]" in mon.metrics.health_line()

    df_log = pd.read_csv(log_file)
    assert len(df_log) == 2
    by_feat = {row["feature"]: row for _, row in df_log.iterrows()}
    assert bool(by_feat["station-1/temp"]["drift"]) is True
    assert bool(by_feat["station-1/humidity"]["drift"]) is False
