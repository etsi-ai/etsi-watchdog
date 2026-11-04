# etsi/watchdog/streaming/alerts.py

import time
import queue
import threading
from typing import Optional, Sequence
from etsi.watchdog.logger import log_drift
from etsi.watchdog.slack_notifier import SlackNotifier
from .windows.engine import Marker
from .metrics import StreamingMetrics


class AlertDispatcher:
    """
    Dedicated background alert dispatcher thread (D-8).
    Decouples synchronous logging and Slack API calls from the consumer poll loop.
    Enforces exponential backoff and idempotency markers.
    """

    def __init__(
        self,
        log_path: str,
        notifier: Optional[SlackNotifier] = None,
        metrics: Optional[StreamingMetrics] = None,
        max_queue_size: int = 1000,
    ):
        self.log_path: str = log_path
        self.notifier: Optional[SlackNotifier] = notifier
        self.metrics: StreamingMetrics = metrics or StreamingMetrics()
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background worker thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="alert-dispatcher")
        self._thread.start()

    def enqueue(self, markers: Sequence[Marker]) -> None:
        """Enqueue flushed markers for asynchronous logging and alerting."""
        for m in markers:
            try:
                self.queue.put_nowait(m)
                self.metrics.alerts_pending += 1
            except queue.Full:
                # Bounded queue protects against unbounded memory growth
                self.metrics.alerts_failed += 1
                print(f"[etsi-watchdog] Alert queue full! Dropping alert for {m.idempotency_key}")

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set() or not self.queue.empty():
            try:
                marker: Marker = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue

            self._dispatch_one(marker)
            self.queue.task_done()
            if self.metrics.alerts_pending > 0:
                self.metrics.alerts_pending -= 1

    def _dispatch_one(self, marker: Marker) -> None:
        # 1. Log drift result to CSV (R6: encode stream_id into feature)
        composite_feature = f"{marker.stream_id}/{marker.feature}"
        try:
            log_drift(marker.result, self.log_path, feature=composite_feature)
        except Exception as e:
            print(f"[etsi-watchdog] Error logging drift to {self.log_path}: {e}")

        # 2. Send Slack notification if drifted and Slack is configured
        if self.notifier and marker.result.is_drifted:
            context = {
                "Stream ID": marker.stream_id,
                "Window Start": marker.window_start,
                "Window End": marker.window_end,
                "Sample Size": marker.result.sample_size,
                "Idempotency Key": marker.idempotency_key,
            }

            # Exponential backoff retry (1s -> 30s, up to 5 attempts)
            delay = 1.0
            success = False
            for attempt in range(1, 6):
                try:
                    success = self.notifier.send_drift_alert(
                        drift_result=marker.result,
                        feature=composite_feature,
                        additional_context=context,
                    )
                    if success:
                        self.metrics.alerts_sent += 1
                        break
                except Exception as e:
                    print(f"[etsi-watchdog] Slack send attempt {attempt} failed: {e}")

                if attempt < 5:
                    time.sleep(delay)
                    delay = min(delay * 2.0, 30.0)

            if not success:
                self.metrics.alerts_failed += 1

    def stop(self, timeout_s: float = 10.0) -> None:
        """Signal worker to stop and wait for in-flight alerts to drain."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout_s)
