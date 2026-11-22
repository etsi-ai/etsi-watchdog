# etsi/watchdog/streaming/windows/keys.py

import re
from typing import Union, Dict, Any

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


def stream_count_key(group: str, stream_id: str) -> str:
    """Stream event count key for count-based windowing."""
    if not validate_stream_id(stream_id):
        raise ValueError(f"Invalid stream_id for key generation: '{stream_id}'")
    return f"wd:v1:{group}:count:{stream_id}"


def win_key(group: str, stream_id: str, feature: str, start_id: Union[int, float, str]) -> str:
    """Window state key supporting both time-based and count-based windows."""
    if not validate_stream_id(stream_id):
        raise ValueError(f"Invalid stream_id for key generation: '{stream_id}'")
    return f"wd:v1:{group}:win:{stream_id}:{feature}:{start_id}"


def win_count_key(group: str, stream_id: str, feature: str, start_count: int) -> str:
    """Count-based window state key."""
    return win_key(group, stream_id, feature, f"c:{start_count}")


def marker_key(
    group: str, stream_id: str, feature: str, algo: str, start_id: Union[int, float, str]
) -> str:
    """Flushed window alert marker key."""
    if not validate_stream_id(stream_id):
        raise ValueError(f"Invalid stream_id for key generation: '{stream_id}'")
    return f"wd:v1:{group}:marker:{stream_id}:{feature}:{algo}:{start_id}"


def marker_count_key(
    group: str, stream_id: str, feature: str, algo: str, start_count: int
) -> str:
    """Count-based flushed window alert marker key."""
    return marker_key(group, stream_id, feature, algo, f"c:{start_count}")


def parse_win_key(key: str) -> Dict[str, Any]:
    """
    Parse a window state key into its component parts:
    group, stream_id, feature, window_type ('count' or 'time'), and window_start.
    """
    parts = key.split(":")
    if len(parts) < 6 or parts[0] != "wd" or parts[1] != "v1" or parts[3] != "win":
        raise ValueError(f"Invalid window key format: '{key}'")

    group = parts[2]
    stream_id = parts[4]
    feature = parts[5]

    if len(parts) == 8 and parts[6] == "c":
        # wd:v1:{group}:win:{stream_id}:{feature}:c:{start_count}
        return {
            "group": group,
            "stream_id": stream_id,
            "feature": feature,
            "window_type": "count",
            "window_start": int(parts[7]),
        }
    elif len(parts) == 7:
        window_id = parts[6]
        if window_id.startswith("c"):
            count_str = window_id[1:]
            return {
                "group": group,
                "stream_id": stream_id,
                "feature": feature,
                "window_type": "count",
                "window_start": int(count_str) if count_str.isdigit() else window_id,
            }
        else:
            return {
                "group": group,
                "stream_id": stream_id,
                "feature": feature,
                "window_type": "time",
                "window_start": float(window_id),
            }
    else:
        raise ValueError(f"Unexpected window key structure: '{key}'")


def owner_key(group: str) -> str:
    """Single-owner lease key."""
    return f"wd:v1:{group}:owner"
