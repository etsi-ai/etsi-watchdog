# etsi/watchdog/streaming/windows/keys.py

import re
from typing import Union

# stream_id restricted to alphanumeric plus . _ - (1 to 128 chars) (D-3, D-9)
STREAM_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def validate_stream_id(stream_id: str) -> bool:
    """Validate that stream_id complies with allowed charset and length constraints."""
    if not isinstance(stream_id, str):
        return False
    return bool(STREAM_ID_PATTERN.match(stream_id))


def offset_key(group: str, topic: str, partition: int) -> str:
    """Partition next_offset key."""
    return f"wd:v1:{group}:offset:{topic}:{partition}"


def wm_key(group: str, stream_id: str) -> str:
    """Stream watermark timestamp key."""
    if not validate_stream_id(stream_id):
        raise ValueError(f"Invalid stream_id for key generation: '{stream_id}'")
    return f"wd:v1:{group}:wm:{stream_id}"


def win_key(group: str, stream_id: str, feature: str, start_ts: Union[int, float]) -> str:
    """Window state key."""
    if not validate_stream_id(stream_id):
        raise ValueError(f"Invalid stream_id for key generation: '{stream_id}'")
    return f"wd:v1:{group}:win:{stream_id}:{feature}:{start_ts}"


def marker_key(
    group: str, stream_id: str, feature: str, algo: str, start_ts: Union[int, float]
) -> str:
    """Flushed window alert marker key."""
    if not validate_stream_id(stream_id):
        raise ValueError(f"Invalid stream_id for key generation: '{stream_id}'")
    return f"wd:v1:{group}:marker:{stream_id}:{feature}:{algo}:{start_ts}"


def owner_key(group: str) -> str:
    """Single-owner lease key."""
    return f"wd:v1:{group}:owner"
