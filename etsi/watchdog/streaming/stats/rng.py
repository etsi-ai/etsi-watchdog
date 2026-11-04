# etsi/watchdog/streaming/stats/rng.py

import hashlib
from typing import Any


class SplitMix64:
    """
    SplitMix64 pseudo-random number generator (PRNG).
    Maintains state as a single 64-bit unsigned integer (uint64).
    Guarantees fast, uniform, deterministic pseudo-random sequences and 
    safe serialization with msgpack (integers < 2**64).
    """

    MASK64: int = 0xFFFFFFFFFFFFFFFF

    def __init__(self, state: int = 0):
        self.state: int = int(state) & self.MASK64

    def next(self) -> int:
        """Advance the PRNG state and return a pseudo-random uint64."""
        self.state = (self.state + 0x9E3779B97F4A7C15) & self.MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self.MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self.MASK64
        return (z ^ (z >> 31)) & self.MASK64

    def next_int(self, n: int) -> int:
        """Return a pseudo-random integer in range [0, n - 1]."""
        if n <= 0:
            raise ValueError(f"Upper bound must be positive, got {n}")
        return self.next() % n

    def next_float(self) -> float:
        """Return a pseudo-random float in range [0.0, 1.0)."""
        return (self.next() >> 11) * (1.0 / 9007199254740992.0)

    @staticmethod
    def seed(*parts: Any) -> int:
        """
        Derive a deterministic 64-bit integer seed from multiple components
        using BLAKE2b (e.g. base_seed, stream_id, feature, window_start).
        Independent of process-level Python hash seed randomization (D-1, PR-11).
        """
        key_str = "|".join(str(p) for p in parts)
        digest = hashlib.blake2b(key_str.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, byteorder="big", signed=False)
