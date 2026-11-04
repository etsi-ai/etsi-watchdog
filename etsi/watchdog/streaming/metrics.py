# etsi/watchdog/streaming/metrics.py

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class StreamingMetrics:
    """
    Observable service metrics and health line formatting (D-12, §10.4).
    """
    events_in: int = 0
    events_rejected: Dict[str, int] = field(default_factory=lambda: {
        "bad_json": 0,
        "bad_stream_id": 0,
        "bad_event_ts": 0,
        "ts_future": 0,
        "stream_cap": 0,
    })
    features_skipped: Dict[str, int] = field(default_factory=lambda: {
        "feature_missing": 0,
        "feature_nonnumeric": 0,
        "feature_nonfinite": 0,
    })
    late_dropped: int = 0
    windows_flushed: int = 0
    windows_skipped_insufficient: int = 0
    checkpoints: int = 0
    checkpoint_ms_last: float = 0.0
    checkpoint_bytes_last: int = 0
    alerts_sent: int = 0
    alerts_failed: int = 0
    alerts_pending: int = 0
    offset_resets: int = 0
    streams_active: int = 0
    streams_idle: int = 0
    lag_events: int = 0

    def record_reject(self, reason: str) -> None:
        self.events_rejected[reason] = self.events_rejected.get(reason, 0) + 1

    def record_feature_skips(self, skips: Dict[str, int]) -> None:
        for reason, count in skips.items():
            self.features_skipped[reason] = self.features_skipped.get(reason, 0) + count

    def health_line(self) -> str:
        """Formatted health output matching §10.4 specification."""
        total_rejected = sum(self.events_rejected.values())
        mb = self.checkpoint_bytes_last / 1_000_000.0
        return (
            f"[etsi-watchdog] events={self.events_in} "
            f"rejected={total_rejected} "
            f"late={self.late_dropped} "
            f"flushed={self.windows_flushed} "
            f"ckpt={self.checkpoints}({self.checkpoint_ms_last:.0f}ms,{mb:.1f}MB) "
            f"alerts sent={self.alerts_sent} pending={self.alerts_pending} failed={self.alerts_failed} "
            f"streams={self.streams_active} idle={self.streams_idle}"
        )
