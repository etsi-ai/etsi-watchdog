# tests/streaming/test_windows_tumbling.py

import pytest
import numpy as np
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.stats.psi_incremental import PsiStat
from etsi.watchdog.streaming.windows.engine import WindowEngine, Event


@pytest.fixture
def test_setup():
    rng = np.random.default_rng(42)
    ref = rng.normal(0, 1, 1000)
    ref_prof = ReferenceProfile(ref, feature="feat1", buckets=10)
    references = {"feat1": ref_prof}

    def engine_factory(min_events=5):
        return WindowEngine(
            window_size=60.0,
            stat_factory=lambda b, e: PsiStat(b, e),
            references=references,
            threshold=0.2,
            grace=30.0,
            min_events=min_events,
            algo="psi",
        )

    return engine_factory


def test_epoch_alignment(test_setup):
    """Epoch alignment is strictly mathematical floor(ts/size)*size."""
    engine = test_setup()
    # Timestamps at 125.0 and 179.99 for window_size 60 must map to window 120.0
    engine.apply(Event(stream_id="s1", ts=125.0, values={"feat1": 0.5}))
    engine.apply(Event(stream_id="s1", ts=179.9, values={"feat1": 0.6}))

    open_keys = list(engine.open_windows.keys())
    assert len(open_keys) == 1
    assert open_keys[0] == ("s1", "feat1", 120.0)


def test_window_boundaries_and_flush(test_setup):
    """
    With window_size=60 and grace=30:
    Window [0, 60) flushes only when watermark reaches 60 + 30 = 90.
    """
    engine = test_setup(min_events=2)

    # Ingest events in [0, 60)
    engine.apply(Event(stream_id="s1", ts=10.0, values={"feat1": 0.1}))
    engine.apply(Event(stream_id="s1", ts=40.0, values={"feat1": 0.2}))

    # Watermark is 40.0; window [0, 60) must remain open
    assert len(engine.open_windows) == 1
    assert len(engine.pending_markers) == 0

    # Ingest event in [60, 120) at t=80
    engine.apply(Event(stream_id="s1", ts=80.0, values={"feat1": 0.3}))
    # Watermark is 80 < 90; window [0, 60) is still open
    assert len(engine.pending_markers) == 0

    # Advance watermark to 90.0 with event at 90.0
    engine.apply(Event(stream_id="s1", ts=90.0, values={"feat1": 0.4}))
    # Window [0, 60) is now due and must flush!
    assert len(engine.pending_markers) == 1
    marker = engine.pending_markers[0]
    assert marker.stream_id == "s1"
    assert marker.feature == "feat1"
    assert marker.window_start == 0.0
    assert marker.window_end == 60.0
    assert marker.result.sample_size == 2


def test_out_of_order_within_grace(test_setup):
    """
    Events arriving out-of-order within the 30s grace period are accepted
    into their original open window.
    """
    engine = test_setup(min_events=2)

    # First event at t=70 (window [60, 120)) sets watermark to 70
    engine.apply(Event(stream_id="s1", ts=70.0, values={"feat1": 1.0}))

    # Second event arriving late at t=45 (window [0, 60))
    # Since ws(0) + 60 + 30 = 90 > wm(70), window [0, 60) is still open
    engine.apply(Event(stream_id="s1", ts=45.0, values={"feat1": 0.5}))

    assert engine.late_dropped == 0
    assert ("s1", "feat1", 0.0) in engine.open_windows
    assert ("s1", "feat1", 60.0) in engine.open_windows


def test_late_drop_beyond_grace(test_setup):
    """
    Events arriving after the window has already flushed are dropped and counted.
    """
    engine = test_setup(min_events=1)

    # Ingest t=10
    engine.apply(Event(stream_id="s1", ts=10.0, values={"feat1": 0.1}))
    # Advance watermark past flush point (>= 90)
    engine.apply(Event(stream_id="s1", ts=95.0, values={"feat1": 0.2}))
    assert engine.windows_flushed == 1

    # Now an event arrives at t=20 (its window [0, 60) flushed at wm=90)
    engine.apply(Event(stream_id="s1", ts=20.0, values={"feat1": 0.3}))

    assert engine.late_dropped == 1
    # Watermark did not regress
    assert engine.watermarks["s1"] == 95.0


def test_per_stream_watermark_isolation(test_setup):
    """Watermarks and windows are strictly isolated per stream_id."""
    engine = test_setup(min_events=1)

    engine.apply(Event(stream_id="stream-A", ts=10.0, values={"feat1": 0.1}))
    engine.apply(Event(stream_id="stream-B", ts=150.0, values={"feat1": 0.2}))

    assert engine.watermarks["stream-A"] == 10.0
    assert engine.watermarks["stream-B"] == 150.0

    # Stream-A window is not flushed
    assert ("stream-A", "feat1", 0.0) in engine.open_windows
    # Stream-B window [0, 60) is flushed (150 > 90)
    assert ("stream-B", "feat1", 0.0) not in engine.open_windows


def test_min_events_per_window_skip(test_setup):
    """Windows with fewer events than min_events emit no result and increment counter."""
    engine = test_setup(min_events=50)

    # Only send 5 events into window [0, 60)
    for t in [10.0, 20.0, 30.0, 40.0, 50.0]:
        engine.apply(Event(stream_id="s1", ts=t, values={"feat1": 0.1}))

    # Flush window by advancing watermark
    engine.apply(Event(stream_id="s1", ts=100.0, values={"feat1": 0.2}))

    assert len(engine.pending_markers) == 0
    assert engine.windows_skipped_insufficient == 1


def test_deterministic_replays(test_setup):
    """Replaying the identical event sequence produces identical results."""
    engine1 = test_setup(min_events=5)
    engine2 = test_setup(min_events=5)

    events = [
        Event(stream_id="s1", ts=float(t), values={"feat1": float(np.sin(t))})
        for t in range(0, 200, 5)
    ]

    for ev in events:
        engine1.apply(ev)
        engine2.apply(ev)

    assert len(engine1.pending_markers) == len(engine2.pending_markers)
    for m1, m2 in zip(engine1.pending_markers, engine2.pending_markers):
        assert m1.idempotency_key == m2.idempotency_key
        assert m1.result.score == m2.result.score
