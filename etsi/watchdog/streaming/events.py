# etsi/watchdog/streaming/events.py

import json
import math
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Dict, Set, Optional, Tuple, Union
from .windows.keys import validate_stream_id
from .windows.engine import Event

# Matches ISO-8601 offset like +00:00, -05:00, +0000, or Z
ISO_OFFSET_PATTERN = re.compile(r"([+-]\d{2}:?\d{2}|Z)$")


@dataclass
class Reject:
    reason: str
    message: str


def parse_iso_timestamp(ts_str: str) -> float:
    """
    Parse ISO-8601 timestamp string in a Python 3.8-compatible manner.
    Requires explicit timezone indicator (Z or offset); rejects naive timestamps (D-4, §10.1).
    """
    if not isinstance(ts_str, str):
        raise ValueError("Timestamp must be a string or number")

    s = ts_str.strip()
    match = ISO_OFFSET_PATTERN.search(s)
    if not match:
        raise ValueError("Timestamp must contain explicit timezone offset (e.g. 'Z' or '+00:00')")

    # Normalize 'Z' to '+00:00'
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    # Normalize fractional seconds to 6 digits (microseconds) for Python 3.8-3.10
    # Pattern: separator '.' followed by digits before the timezone offset
    offset_sign_idx = max(s.rfind("+"), s.rfind("-"))
    main_part = s[:offset_sign_idx]
    tz_part = s[offset_sign_idx:]

    if "." in main_part:
        dt_base, frac = main_part.split(".", 1)
        # Pad or truncate fraction to 6 digits
        frac = (frac + "000000")[:6]
        main_part = f"{dt_base}.{frac}"

    normalized_iso = f"{main_part}{tz_part}"
    dt = datetime.fromisoformat(normalized_iso)
    return float(dt.timestamp())


def parse_and_validate(
    raw: bytes,
    monitored_features: Set[str],
    now: float,
    max_future_skew_seconds: float = 3600.0,
    max_streams: int = 1000,
    known_streams: Optional[Set[str]] = None,
) -> Tuple[Union[Event, Reject], Dict[str, int]]:
    """
    Validate raw message against schema constraints (D-4, D-9, §10.1).
    Returns (Event | Reject, feature_skip_counters).
    Message-level rejects allow partition offset to advance without wedging.
    """
    feature_skips: Dict[str, int] = {
        "feature_missing": 0,
        "feature_nonnumeric": 0,
        "feature_nonfinite": 0,
    }

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return Reject("bad_json", f"JSON decode error: {e}"), feature_skips

    if not isinstance(data, dict):
        return Reject("bad_json", "Top-level payload must be a JSON object"), feature_skips

    # 1. Validate stream_id
    stream_id = data.get("stream_id")
    if not stream_id or not validate_stream_id(stream_id):
        return Reject("bad_stream_id", f"Missing or invalid stream_id: {stream_id}"), feature_skips

    # 2. Check stream capacity cap
    if known_streams is not None and stream_id not in known_streams:
        if len(known_streams) >= max_streams:
            return Reject("stream_cap", f"Max streams cap ({max_streams}) reached"), feature_skips

    # 3. Validate timestamp
    raw_ts = data.get("event_ts")
    if raw_ts is None:
        return Reject("bad_event_ts", "Missing event_ts field"), feature_skips

    try:
        if isinstance(raw_ts, (int, float)):
            event_ts = float(raw_ts)
        elif isinstance(raw_ts, str):
            event_ts = parse_iso_timestamp(raw_ts)
        else:
            return Reject("bad_event_ts", f"Unrecognized event_ts type: {type(raw_ts).__name__}"), feature_skips
    except Exception as e:
        return Reject("bad_event_ts", f"Unparseable event_ts: {e}"), feature_skips

    # Bounds check [0, 1e11)
    if event_ts < 0.0 or event_ts >= 1e11:
        return Reject("bad_event_ts", f"Timestamp {event_ts} out of valid epoch range [0, 1e11)"), feature_skips

    # 4. Watermark poisoning / future skew guard (D-9)
    if max_future_skew_seconds > 0.0:
        if event_ts > (now + max_future_skew_seconds):
            return Reject(
                "ts_future",
                f"event_ts ({event_ts}) exceeds now ({now}) + max_future_skew ({max_future_skew_seconds})",
            ), feature_skips

    # 5. Feature-level extraction and validation (skip per feature, offset still advances)
    valid_values: Dict[str, float] = {}
    for feat in monitored_features:
        if feat not in data:
            feature_skips["feature_missing"] += 1
            continue

        val = data[feat]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            feature_skips["feature_nonnumeric"] += 1
            continue

        fval = float(val)
        if not math.isfinite(fval):
            feature_skips["feature_nonfinite"] += 1
            continue

        valid_values[feat] = fval

    return Event(stream_id=stream_id, ts=event_ts, values=valid_values), feature_skips
