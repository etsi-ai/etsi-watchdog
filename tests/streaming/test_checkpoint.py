# tests/streaming/test_checkpoint.py

import pytest
import fakeredis
from unittest.mock import MagicMock
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.streaming.checkpoint import CheckpointWriter
from etsi.watchdog.streaming.state.codec import decode
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore
from etsi.watchdog.streaming.state.redis_store import RedisStateStore
from etsi.watchdog.streaming.state.base import FenceMismatchError
from etsi.watchdog.streaming.windows.engine import WindowEngine, Marker
from etsi.watchdog.streaming.windows.keys import offset_key, wm_key, win_key, marker_key
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from etsi.watchdog.streaming.stats.psi_incremental import PsiStat


@pytest.fixture
def mock_engine():
    ref = ReferenceProfile([1.0, 2.0, 3.0, 4.0, 5.0], feature="f1", buckets=5, seed=0)
    engine = WindowEngine(
        window_size=60.0,
        stat_factory=lambda b, e: PsiStat(b, e),
        references={"f1": ref},
        threshold=0.2,
    )
    # Simulate open window
    stat = PsiStat(ref.buckets, ref.edges_list)
    stat.update(2.5)
    engine.open_windows[("stream_1", "f1", 0.0)] = stat
    engine.dirty.add(("stream_1", "f1", 0.0))
    engine.watermarks["stream_1"] = 35.0
    engine.dirty_streams.add("stream_1")

    # Simulate pending marker from flushed window
    res = DriftResult(
        method="psi",
        score=0.05,
        threshold=0.2,
        details={"bins": []},
        sample_size=100,
    )
    marker = Marker(
        stream_id="stream_1",
        feature="f1",
        algo="psi",
        window_start=0.0,
        window_end=60.0,
        result=res,
    )
    engine.pending_markers.append(marker)
    engine.flushed.add(("stream_1", "f1", -60.0))
    return engine


def test_checkpoint_atomic_execution_and_ordering(mock_engine):
    store = InMemoryStateStore()
    dispatcher = MagicMock()
    source = MagicMock()

    writer = CheckpointWriter(
        group="test_group",
        store=store,
        engine=mock_engine,
        dispatcher=dispatcher,
    )

    offsets = {("topic_a", 0): 101, ("topic_a", 1): 205}
    summary = writer.checkpoint(offsets=offsets, source=source, reason="test")

    assert summary["puts"] > 0
    assert summary["deletes"] == 1
    assert summary["markers"] == 1

    # Verify store contents
    # 1. Offset
    off_bytes = store.get(offset_key("test_group", "topic_a", 0))
    assert off_bytes is not None
    assert decode(off_bytes) == 101

    # 2. Watermark
    wm_bytes = store.get(wm_key("test_group", "stream_1"))
    assert wm_bytes is not None
    assert decode(wm_bytes) == 35.0

    # 3. Open window state
    win_bytes = store.get(win_key("test_group", "stream_1", "f1", 0.0))
    assert win_bytes is not None
    decoded_win = decode(win_bytes)
    assert decoded_win["schema_version"] == 1
    assert decoded_win["algo"] == "psi"

    # 4. Marker state
    m_bytes = store.get(marker_key("test_group", "stream_1", "f1", "psi", 0.0))
    assert m_bytes is not None
    decoded_m = decode(m_bytes)
    assert decoded_m["status"] == "pending"
    assert decoded_m["idempotency_key"] == "stream_1:f1:psi:0.0"

    # Verify post-persistence actions
    source.commit.assert_called_once_with(offsets)
    dispatcher.enqueue.assert_called_once()
    assert len(mock_engine.pending_markers) == 0
    assert len(mock_engine.dirty) == 0
    assert len(mock_engine.flushed) == 0


def test_checkpoint_failure_preserves_state(mock_engine):
    fake_client = fakeredis.FakeRedis(decode_responses=False)
    owner_key = "wd:v1:test_group:owner"
    fake_client.set(owner_key, b"real-token")

    store = RedisStateStore(redis_client=fake_client, owner_key=owner_key)
    dispatcher = MagicMock()
    source = MagicMock()

    # Create writer with wrong fence token
    writer = CheckpointWriter(
        group="test_group",
        store=store,
        engine=mock_engine,
        dispatcher=dispatcher,
        lease_token="zombie-token",
    )

    offsets = {("topic_a", 0): 500}

    with pytest.raises(FenceMismatchError):
        writer.checkpoint(offsets=offsets, source=source)

    # Assert nothing was dispatched or committed on crash/fence mismatch
    source.commit.assert_not_called()
    dispatcher.enqueue.assert_not_called()

    # Assert engine dirty sets are NOT cleared so state is not lost
    assert len(mock_engine.dirty) > 0
    assert len(mock_engine.pending_markers) > 0
