# etsi/watchdog/streaming/windows/engine.py

from dataclasses import dataclass
from typing import Dict, Any, List, Set, Tuple, Optional, Callable, Union
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.streaming.stats.base import StreamingStat
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from .tumbling import TumblingWindowSlice, CountTumblingWindowSlice


@dataclass
class Event:
    stream_id: str
    ts: float
    values: Dict[str, float]


@dataclass
class Marker:
    stream_id: str
    feature: str
    algo: str
    window_start: Union[float, int, str]
    window_end: Union[float, int, str]
    result: DriftResult

    @property
    def idempotency_key(self) -> str:
        return f"{self.stream_id}:{self.feature}:{self.algo}:{self.window_start}"


class WindowEngine:
    """
    Streaming windowing engine supporting both event-time tumbling windows (D-7)
    and count-based tumbling windows (Issue #53, window_size_events).
    """

    def __init__(
        self,
        window_size: float = 60.0,
        stat_factory: Callable[..., StreamingStat] = None,
        references: Dict[str, ReferenceProfile] = None,
        threshold: float = 0.2,
        grace: float = 30.0,
        min_events: int = 50,
        algo: str = "psi",
        window_size_events: Optional[int] = None,
    ):
        self.window_size: float = float(window_size)
        self.window_size_events: Optional[int] = int(window_size_events) if window_size_events is not None else None
        self.is_count_based: bool = self.window_size_events is not None and self.window_size_events > 0

        self.stat_factory = stat_factory
        self.references: Dict[str, ReferenceProfile] = references or {}
        self.threshold: float = float(threshold)
        self.grace: float = float(grace)
        self.min_events: int = int(min_events)
        self.algo: str = algo

        # State collections
        self.open_windows: Dict[Tuple[str, str, Any], StreamingStat] = {}
        self.watermarks: Dict[str, float] = {}
        self.stream_counts: Dict[str, int] = {}

        # Tracking sets for checkpointing
        self.dirty: Set[Tuple[str, str, Any]] = set()
        self.dirty_streams: Set[str] = set()
        self.flushed: Set[Tuple[str, str, Any]] = set()
        self.pending_markers: List[Marker] = []

        # Metric counters
        self.late_dropped: int = 0
        self.windows_flushed: int = 0
        self.windows_skipped_insufficient: int = 0

    def apply(self, ev: Event) -> None:
        """Apply an already-validated event to open windows."""
        if self.is_count_based:
            self._apply_count(ev)
        else:
            self._apply_time(ev)

    def _apply_count(self, ev: Event) -> None:
        """Count-based tumbling window processing (Issue #53)."""
        curr_count = self.stream_counts.get(ev.stream_id, 0)
        self.stream_counts[ev.stream_id] = curr_count + 1
        self.dirty_streams.add(ev.stream_id)

        ws_int = CountTumblingWindowSlice.count_start(curr_count, self.window_size_events)
        ws_id = f"c:{ws_int}"

        for f, val in ev.values.items():
            if f not in self.references:
                continue

            key = (ev.stream_id, f, ws_id)
            if key not in self.open_windows:
                ref_prof = self.references[f]
                if hasattr(self.stat_factory, "create_stat"):
                    self.open_windows[key] = self.stat_factory.create_stat(
                        ref_prof=ref_prof,
                        stream_id=ev.stream_id,
                        feature=f,
                        window_start=ws_id,
                    )
                else:
                    self.open_windows[key] = self.stat_factory(ref_prof.buckets, ref_prof.edges_list)

            self.open_windows[key].update(float(val))
            self.dirty.add(key)

        # Flush deterministically when this event completes the window slice
        if (curr_count + 1) % self.window_size_events == 0:
            self._flush_count_window(ev.stream_id, ws_int)

    def _flush_count_window(self, stream_id: str, ws_int: int) -> None:
        """Flush a completed count-based window slice."""
        ws_id = f"c:{ws_int}"
        window_end_int = ws_int + self.window_size_events
        window_end_id = f"c:{window_end_int}"

        due_keys = [
            (s, f, w)
            for (s, f, w) in list(self.open_windows.keys())
            if s == stream_id and w == ws_id
        ]
        due_keys.sort(key=lambda k: k[1])

        for key in due_keys:
            s, f, _ = key
            stat = self.open_windows.pop(key)
            self.dirty.discard(key)
            self.flushed.add(key)

            if stat.n < self.min_events:
                self.windows_skipped_insufficient += 1
            else:
                ref_prof = self.references[f]
                meta = {
                    "stream_id": s,
                    "window_start": ws_id,
                    "window_end": window_end_id,
                    "window_start_count": ws_int,
                    "window_end_count": window_end_int,
                    "n_events": stat.n,
                    "idempotency_key": f"{s}:{f}:{self.algo}:{ws_id}",
                }
                res = stat.finalize(ref_prof, threshold=self.threshold, meta=meta)
                marker = Marker(
                    stream_id=s,
                    feature=f,
                    algo=self.algo,
                    window_start=ws_id,
                    window_end=window_end_id,
                    result=res,
                )
                self.pending_markers.append(marker)
                self.windows_flushed += 1

    def _apply_time(self, ev: Event) -> None:
        """Event-time tumbling window processing (D-7)."""
        wm = self.watermarks.get(ev.stream_id, float("-inf"))
        ws = TumblingWindowSlice.epoch_start(ev.ts, self.window_size)

        # Check lateness: if window has already flushed, drop late event (D-7)
        if ws + self.window_size + self.grace <= wm:
            self.late_dropped += 1
            return

        for f, val in ev.values.items():
            if f not in self.references:
                continue

            key = (ev.stream_id, f, ws)
            if key not in self.open_windows:
                ref_prof = self.references[f]
                if hasattr(self.stat_factory, "create_stat"):
                    self.open_windows[key] = self.stat_factory.create_stat(
                        ref_prof=ref_prof,
                        stream_id=ev.stream_id,
                        feature=f,
                        window_start=ws,
                    )
                else:
                    self.open_windows[key] = self.stat_factory(ref_prof.buckets, ref_prof.edges_list)

            self.open_windows[key].update(float(val))
            self.dirty.add(key)

        # Monotonically advance watermark for this stream
        if ev.ts > wm:
            self.watermarks[ev.stream_id] = ev.ts
            self.dirty_streams.add(ev.stream_id)

        self.flush_due(ev.stream_id)

    def flush_due(self, stream_id: str) -> None:
        """Flush any windows whose end + grace has been reached by the stream's watermark."""
        if self.is_count_based:
            return

        wm = self.watermarks.get(stream_id, float("-inf"))
        if wm == float("-inf"):
            return

        due_keys = [
            (s, f, ws)
            for (s, f, ws) in self.open_windows
            if s == stream_id and (ws + self.window_size + self.grace <= wm)
        ]

        # Flush in deterministic chronological order
        due_keys.sort(key=lambda k: k[2])

        for key in due_keys:
            s, f, ws = key
            stat = self.open_windows.pop(key)
            self.dirty.discard(key)
            self.flushed.add(key)

            if stat.n < self.min_events:
                self.windows_skipped_insufficient += 1
            else:
                ref_prof = self.references[f]
                window_end = ws + self.window_size
                meta = {
                    "stream_id": s,
                    "window_start": ws,
                    "window_end": window_end,
                    "n_events": stat.n,
                    "idempotency_key": f"{s}:{f}:{self.algo}:{ws}",
                }
                res = stat.finalize(ref_prof, threshold=self.threshold, meta=meta)
                marker = Marker(
                    stream_id=s,
                    feature=f,
                    algo=self.algo,
                    window_start=ws,
                    window_end=window_end,
                    result=res,
                )
                self.pending_markers.append(marker)
                self.windows_flushed += 1

    def clear_stream(self, stream_id: str) -> None:
        """
        Evict in-memory state for a revoked stream upon rebalance (Task 3.4, Issue #51).
        Discards open windows, watermark, stream count, and dirty tracking.
        """
        keys_to_remove = [k for k in self.open_windows if k[0] == stream_id]
        for k in keys_to_remove:
            self.open_windows.pop(k, None)
            self.dirty.discard(k)
            self.flushed.discard(k)
        self.watermarks.pop(stream_id, None)
        self.stream_counts.pop(stream_id, None)
        self.dirty_streams.discard(stream_id)
