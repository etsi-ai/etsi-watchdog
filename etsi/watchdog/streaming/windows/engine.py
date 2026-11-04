# etsi/watchdog/streaming/windows/engine.py

from dataclasses import dataclass
from typing import Dict, Any, List, Set, Tuple, Optional, Callable
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.streaming.stats.base import StreamingStat
from etsi.watchdog.streaming.stats.reference import ReferenceProfile
from .tumbling import TumblingWindowSlice


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
    window_start: float
    window_end: float
    result: DriftResult

    @property
    def idempotency_key(self) -> str:
        return f"{self.stream_id}:{self.feature}:{self.algo}:{self.window_start}"


class WindowEngine:
    """
    Event-time streaming windowing engine (D-7).
    Enforces epoch-aligned tumbling windows, per-stream monotonic watermarks,
    grace periods, and dropped late events.
    """

    def __init__(
        self,
        window_size: float,
        stat_factory: Callable[..., StreamingStat],
        references: Dict[str, ReferenceProfile],
        threshold: float = 0.2,
        grace: float = 30.0,
        min_events: int = 50,
        algo: str = "psi",
    ):
        self.window_size: float = float(window_size)
        self.stat_factory = stat_factory
        self.references: Dict[str, ReferenceProfile] = references
        self.threshold: float = float(threshold)
        self.grace: float = float(grace)
        self.min_events: int = int(min_events)
        self.algo: str = algo

        # State collections
        self.open_windows: Dict[Tuple[str, str, float], StreamingStat] = {}
        self.watermarks: Dict[str, float] = {}

        # Tracking sets for checkpointing
        self.dirty: Set[Tuple[str, str, float]] = set()
        self.dirty_streams: Set[str] = set()
        self.flushed: Set[Tuple[str, str, float]] = set()
        self.pending_markers: List[Marker] = []

        # Metric counters
        self.late_dropped: int = 0
        self.windows_flushed: int = 0
        self.windows_skipped_insufficient: int = 0

    def apply(self, ev: Event) -> None:
        """Apply an already-validated event to open windows and advance watermark."""
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
