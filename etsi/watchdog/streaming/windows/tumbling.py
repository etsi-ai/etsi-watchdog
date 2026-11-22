# etsi/watchdog/streaming/windows/tumbling.py

import math
from typing import Dict, Any
from etsi.watchdog.streaming.stats.base import StreamingStat


class TumblingWindowSlice:
    """
    State slice for a single tumbling window over a (stream_id, feature, window_start).
    Epoch-aligned non-overlapping window [start, start + size).
    """

    def __init__(
        self,
        stream_id: str,
        feature: str,
        window_start: float,
        window_size: float,
        stat: StreamingStat,
    ):
        self.stream_id: str = stream_id
        self.feature: str = feature
        self.window_start: float = float(window_start)
        self.window_size: float = float(window_size)
        self.window_end: float = float(window_start + window_size)
        self.stat: StreamingStat = stat

    def is_due(self, watermark: float, grace: float) -> bool:
        """Window flushes when stream watermark advances to start + size + grace (D-7)."""
        return watermark >= (self.window_end + grace)

    @staticmethod
    def epoch_start(ts: float, size: float) -> float:
        """Compute epoch-aligned window start timestamp."""
        return float(math.floor(ts / size) * size)


class CountTumblingWindowSlice:
    """
    State slice for a single count-based tumbling window over a (stream_id, feature, window_start_count).
    Fixed-count non-overlapping window [start_count, start_count + size_events).
    """

    def __init__(
        self,
        stream_id: str,
        feature: str,
        window_start: int,
        window_size_events: int,
        stat: StreamingStat,
    ):
        self.stream_id: str = stream_id
        self.feature: str = feature
        self.window_start: int = int(window_start)
        self.window_size_events: int = int(window_size_events)
        self.window_end: int = int(window_start + window_size_events)
        self.stat: StreamingStat = stat

    def is_due(self) -> bool:
        """Window flushes when stat.n reaches window_size_events."""
        return self.stat.n >= self.window_size_events

    @staticmethod
    def count_start(event_idx: int, size_events: int) -> int:
        """Compute window start count."""
        return int((event_idx // size_events) * size_events)
