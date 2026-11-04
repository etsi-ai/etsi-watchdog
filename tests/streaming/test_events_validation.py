# tests/streaming/test_events_validation.py

import json
import time
import pytest
from etsi.watchdog.streaming.events import parse_and_validate, parse_iso_timestamp, Reject
from etsi.watchdog.streaming.windows.engine import Event


def test_parse_iso_timestamp():
    """Verify ISO timestamp parsing across valid, normalized, and naive formats."""
    # Zulu time with 3-digit millisecond
    ts1 = parse_iso_timestamp("2026-08-26T14:00:00.123Z")
    assert ts1 > 0

    # With offset
    ts2 = parse_iso_timestamp("2026-08-26T14:00:00+00:00")
    assert ts2 > 0

    # Non-standard fractional digits (e.g. 2 digits or 5 digits) normalized cleanly
    ts3 = parse_iso_timestamp("2026-08-26T14:00:00.12Z")
    assert ts3 > 0

    # Naive timestamp without timezone must be rejected
    with pytest.raises(ValueError, match="timezone"):
        parse_iso_timestamp("2026-08-26T14:00:00")


def test_events_poison_message_matrix():
    """Test every Reject reason and ensure invalid payloads do not crash the parser."""
    features = {"temp", "humidity"}
    now = 1700000000.0

    # 1. Bad JSON
    res, _ = parse_and_validate(b"not json", features, now)
    assert isinstance(res, Reject)
    assert res.reason == "bad_json"

    # 2. Bad stream_id
    payload_bad_id = json.dumps({"stream_id": "invalid|id", "event_ts": now, "temp": 20.0}).encode()
    res, _ = parse_and_validate(payload_bad_id, features, now)
    assert isinstance(res, Reject)
    assert res.reason == "bad_stream_id"

    # 3. Bad event_ts (missing or non-timestamp)
    payload_bad_ts = json.dumps({"stream_id": "s1", "event_ts": "not-a-date", "temp": 20.0}).encode()
    res, _ = parse_and_validate(payload_bad_ts, features, now)
    assert isinstance(res, Reject)
    assert res.reason == "bad_event_ts"

    # 4. Far-future timestamp exceeding max_future_skew_seconds (watermark guard)
    future_ts = now + 7200.0  # 2 hours in future (skew limit is 3600s)
    payload_future = json.dumps({"stream_id": "s1", "event_ts": future_ts, "temp": 20.0}).encode()
    res, _ = parse_and_validate(payload_future, features, now, max_future_skew_seconds=3600.0)
    assert isinstance(res, Reject)
    assert res.reason == "ts_future"

    # 5. Stream capacity cap exceeded
    known = {f"stream_{i}" for i in range(10)}
    payload_cap = json.dumps({"stream_id": "new_stream_11", "event_ts": now, "temp": 20.0}).encode()
    res, _ = parse_and_validate(payload_cap, features, now, max_streams=10, known_streams=known)
    assert isinstance(res, Reject)
    assert res.reason == "stream_cap"


def test_events_feature_level_skips():
    """Feature-level missing or corrupt values skip the feature without dropping the message."""
    features = {"f_missing", "f_bool", "f_str", "f_nan", "f_valid"}
    now = 1700000000.0

    payload = {
        "stream_id": "station-01",
        "event_ts": now,
        "f_bool": True,       # bools excluded (C1, §10.1)
        "f_str": "hello",     # non-numeric
        "f_nan": float("nan"),# non-finite
        "f_valid": 42.5,
    }
    raw = json.dumps(payload).encode()
    event, skips = parse_and_validate(raw, features, now)

    assert isinstance(event, Event)
    assert event.stream_id == "station-01"
    assert event.values == {"f_valid": 42.5}

    assert skips["feature_missing"] == 1    # f_missing
    assert skips["feature_nonnumeric"] == 2 # f_bool, f_str
    assert skips["feature_nonfinite"] == 1  # f_nan
