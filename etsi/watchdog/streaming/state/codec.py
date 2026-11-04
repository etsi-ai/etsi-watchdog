# etsi/watchdog/streaming/state/codec.py

import json
from typing import Any, Dict

SCHEMA_VERSION: int = 1

try:
    import msgpack
    _HAS_MSGPACK = True
except ImportError:
    _HAS_MSGPACK = False


def encode(data: Any, schema_version: int = SCHEMA_VERSION) -> bytes:
    """
    Encode state payload into versioned binary format (D-3, Task 4.4).
    Uses msgpack when installed, with JSON fallback.
    Never uses pickle.
    """
    wrapper = {
        "schema_v": int(schema_version),
        "payload": data,
    }

    if _HAS_MSGPACK:
        return msgpack.packb(wrapper, use_bin_type=True)
    else:
        return json.dumps(wrapper).encode("utf-8")


def decode(raw: bytes) -> Any:
    """
    Decode versioned binary payload.
    Rejects unknown future schema versions.
    """
    if _HAS_MSGPACK:
        try:
            wrapper = msgpack.unpackb(raw, raw=False)
        except Exception:
            # Fallback attempt for JSON encoded data
            wrapper = json.loads(raw.decode("utf-8"))
    else:
        wrapper = json.loads(raw.decode("utf-8"))

    if not isinstance(wrapper, dict) or "schema_v" not in wrapper:
        raise ValueError("Invalid state payload: missing schema_v header")

    schema_v = int(wrapper["schema_v"])
    if schema_v > SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version: {schema_v}. "
            f"Max supported version by this binary is {SCHEMA_VERSION}."
        )

    return wrapper["payload"]
