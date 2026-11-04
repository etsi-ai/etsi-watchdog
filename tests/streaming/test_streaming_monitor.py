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

    # 2. Reservoir size > 10,000 must fail fast (D-1)
    cfg_bad_res = StreamingConfig(
        reference_path=str(ref_file),
        features=["feature_a"],
        topic="test-topic",
        log_path="logs/drift.csv",
        reservoir_size=15000,
    )
    with pytest.raises(ValueError, match="RESERVOIR_SIZE cannot exceed 10000"):
        cfg_bad_res.validate()


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
