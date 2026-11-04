# tests/streaming/test_state_layer.py

import pytest
import threading
from etsi.watchdog.streaming.windows.keys import (
    validate_stream_id,
    offset_key,
    wm_key,
    win_key,
    marker_key,
    owner_key,
)
from etsi.watchdog.streaming.state.codec import encode, decode, SCHEMA_VERSION
from etsi.watchdog.streaming.state.memory_store import InMemoryStateStore


def test_key_builders_and_stream_id_validation():
    """Verify key generation and stream_id charset constraints (D-3, D-9)."""
    # Valid stream_ids
    assert validate_stream_id("stream-1") is True
    assert validate_stream_id("stream_2.test") is True
    assert validate_stream_id("A123") is True

    # Invalid stream_ids (rejects pipes, spaces, specials, empty)
    assert validate_stream_id("") is False
    assert validate_stream_id("stream|bad") is False
    assert validate_stream_id("stream with space") is False
    assert validate_stream_id("a" * 129) is False

    group = "grp"
    assert offset_key(group, "topic-a", 0) == "wd:v1:grp:offset:topic-a:0"
    assert wm_key(group, "stream-1") == "wd:v1:grp:wm:stream-1"
    assert win_key(group, "stream-1", "temp", 1000) == "wd:v1:grp:win:stream-1:temp:1000"
    assert marker_key(group, "stream-1", "temp", "psi", 1000) == "wd:v1:grp:marker:stream-1:temp:psi:1000"
    assert owner_key(group) == "wd:v1:grp:owner"

    with pytest.raises(ValueError):
        wm_key(group, "invalid|id")


def test_codec_encode_decode_roundtrip():
    """Verify versioned codec encodes and decodes primitives cleanly."""
    payload = {
        "counts": [10, 20, 30],
        "nested": {"score": 0.45, "algo": "psi"},
        "name": "stream_test",
    }
    raw = encode(payload)
    assert isinstance(raw, bytes)
    restored = decode(raw)
    assert restored == payload


def test_codec_reject_future_schema_version():
    """Codec must reject unknown future schema versions."""
    future_raw = encode({"dummy": 1}, schema_version=99)
    with pytest.raises(ValueError, match="Unsupported schema_version: 99"):
        decode(future_raw)


def test_memory_store_basic_crud():
    """Verify CRUD operations on InMemoryStateStore."""
    store = InMemoryStateStore()
    k1 = "wd:v1:g:win:s1:f1:0"
    v1 = b"binary_data_1"

    assert store.get(k1) is None
    store.write_batch(puts={k1: v1}, deletes=[])
    assert store.get(k1) == v1

    res = store.get_many([k1, "nonexistent"])
    assert res == {k1: v1}

    # List prefix
    k2 = "wd:v1:g:win:s1:f2:0"
    store.write_batch(puts={k2: b"data2"}, deletes=[])
    assert store.list_prefix("wd:v1:g:win:s1:") == [k1, k2]

    # Delete
    store.write_batch(puts={}, deletes=[k1])
    assert store.get(k1) is None
    assert store.get(k2) == b"data2"


def test_memory_store_ttl_expiry_with_injectable_clock():
    """Verify TTL expiry using an injectable test clock."""
    simulated_time = 1000.0

    def clock():
        return simulated_time

    store = InMemoryStateStore(clock=clock)
    k1 = "key_with_ttl"
    k2 = "key_persistent"

    store.write_batch(
        puts={k1: b"val1", k2: b"val2"},
        deletes=[],
        ttl={k1: 30},  # expires at t=1030
    )

    assert store.get(k1) == b"val1"
    assert store.get(k2) == b"val2"

    # Advance clock past TTL
    simulated_time = 1031.0
    assert store.get(k1) is None
    assert store.get(k2) == b"val2"
    assert store.list_prefix("key_") == [k2]


def test_memory_store_fencing():
    """Verify owner lease fencing on write_batch."""
    store = InMemoryStateStore()
    store.owner_token = "lease-token-abc"

    # Write with matching fence
    store.write_batch(puts={"k": b"v"}, deletes=[], fence="lease-token-abc")
    assert store.get("k") == b"v"

    # Write with mismatched fence must raise PermissionError
    with pytest.raises(PermissionError, match="Fencing token mismatch"):
        store.write_batch(puts={"k2": b"v2"}, deletes=[], fence="stale-zombie-token")

    assert store.get("k2") is None


def test_memory_store_snapshot_restore():
    """Verify snapshot and restore mechanism."""
    store = InMemoryStateStore()
    store.owner_token = "token-1"
    store.write_batch(puts={"a": b"1", "b": b"2"}, deletes=[])

    snap = store.snapshot()

    # Mutate store
    store.write_batch(puts={"c": b"3"}, deletes=["a"])
    assert store.get("a") is None
    assert store.get("c") == b"3"

    # Restore
    store.restore(snap)
    assert store.get("a") == b"1"
    assert store.get("b") == b"2"
    assert store.get("c") is None
    assert store.owner_token == "token-1"


def test_memory_store_thread_safety():
    """Multi-threaded concurrency smoke test."""
    store = InMemoryStateStore()
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(100):
                k = f"key_{worker_id}_{i}"
                store.write_batch(puts={k: f"val_{i}".encode()}, deletes=[])
                val = store.get(k)
                assert val == f"val_{i}".encode()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(store.list_prefix("key_")) == 500
