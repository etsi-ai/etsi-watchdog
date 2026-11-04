# etsi/watchdog/streaming/streaming_monitor.py

import time
import os
import pandas as pd
from typing import Optional, Dict, Set, Tuple
from etsi.watchdog.drift.factory import get_drift_function
from etsi.watchdog.slack_notifier import SlackNotifier
from .config import StreamingConfig
from .source import EventSource, RawMessage
from .state.base import StateStore
from .stats.reference import ReferenceProfile
from .windows.engine import WindowEngine, Event
from .events import parse_and_validate, Reject
from .metrics import StreamingMetrics
from .alerts import AlertDispatcher


class StreamingMonitor:
    """
    Streaming drift monitor orchestrating consumer source -> window engine -> state -> alerts (D-12, Task 5.1).
    Structurally parallel to batch Monitor, designed for continuous event stream ingestion.
    """

    def __init__(
        self,
        config: StreamingConfig,
        source: EventSource,
        store: StateStore,
        notifier: Optional[SlackNotifier] = None,
        references: Optional[Dict[str, ReferenceProfile]] = None,
    ):
        self.config: StreamingConfig = config
        self.config.validate()

        self.source: EventSource = source
        self.store: StateStore = store
        self.notifier: Optional[SlackNotifier] = notifier
        self.metrics: StreamingMetrics = StreamingMetrics()

        # 1. Resolve Reference Profiles
        if references is not None:
            self.references = references
        else:
            self.references = self._load_reference_profiles()

        # 2. Resolve Algorithm and Stat Factory from Registry (D-10)
        algo_func = get_drift_function(self.config.algorithm)
        stat_factory = getattr(algo_func, "stat_factory", None)
        if stat_factory is None:
            raise ValueError(f"Algorithm '{self.config.algorithm}' does not define a streaming .stat_factory")

        default_thr = getattr(algo_func, "default_threshold", 0.2)
        self.threshold: float = self.config.threshold if self.config.threshold is not None else default_thr

        # 3. Initialize Window Engine
        self.engine = WindowEngine(
            window_size=self.config.window_seconds,
            stat_factory=stat_factory,
            references=self.references,
            threshold=self.threshold,
            grace=self.config.grace_seconds,
            min_events=self.config.min_events,
            algo=self.config.algorithm,
        )

        # 4. Initialize Alert Dispatcher
        self.dispatcher = AlertDispatcher(
            log_path=self.config.log_path,
            notifier=self.notifier,
            metrics=self.metrics,
        )

        # State tracking
        self.known_streams: Set[str] = set()
        self.offsets: Dict[Tuple[str, int], int] = {}
        self.events_since_checkpoint: int = 0
        self.last_checkpoint_ts: float = time.time()
        self.last_health_ts: float = time.time()
        self._stop_requested: bool = False

    def _load_reference_profiles(self) -> Dict[str, ReferenceProfile]:
        ref_path = self.config.reference_path
        if not os.path.exists(ref_path):
            raise FileNotFoundError(f"Reference file not found: {ref_path}")

        if ref_path.endswith(".parquet"):
            df = pd.read_parquet(ref_path)
        else:
            df = pd.read_csv(ref_path)

        profiles = {}
        for feat in self.config.features:
            if feat not in df.columns:
                raise ValueError(f"Feature '{feat}' not present in reference data {ref_path}")
            vals = df[feat].dropna().values
            profiles[feat] = ReferenceProfile(
                vals,
                feature=feat,
                buckets=self.config.psi_buckets,
                max_samples=self.config.reference_max_samples,
                seed=self.config.seed,
            )
        return profiles

    def checkpoint(self, reason: str = "interval") -> None:
        """
        Execute checkpoint.
        In Milestone 1, flushes pending markers to dispatcher, commits offsets, and clears dirty sets.
        Milestone 2 upgrades this to an atomic CheckpointWriter across the StateStore.
        """
        t0 = time.perf_counter()

        if self.engine.pending_markers:
            markers = list(self.engine.pending_markers)
            self.engine.pending_markers.clear()
            self.dispatcher.enqueue(markers)

        self.engine.dirty.clear()
        self.engine.dirty_streams.clear()
        self.engine.flushed.clear()

        # Advisory commit to message source
        if self.offsets:
            self.source.commit(self.offsets)

        self.events_since_checkpoint = 0
        self.last_checkpoint_ts = time.time()
        self.metrics.checkpoints += 1
        self.metrics.checkpoint_ms_last = (time.perf_counter() - t0) * 1000.0

    def run_once(self, max_events: int = 500, timeout_s: float = 1.0) -> int:
        """Execute a single poll -> validate -> apply -> checkpoint cycle."""
        messages = self.source.poll(max_events=max_events, timeout_s=timeout_s)
        now = time.time()

        for msg in messages:
            self.metrics.events_in += 1
            self.events_since_checkpoint += 1
            self.offsets[(msg.topic, msg.partition)] = msg.offset + 1

            if not msg.value:
                self.metrics.record_reject("bad_json")
                continue

            parsed, skips = parse_and_validate(
                raw=msg.value,
                monitored_features=set(self.config.features),
                now=now,
                max_future_skew_seconds=self.config.max_future_skew_seconds,
                max_streams=self.config.max_streams,
                known_streams=self.known_streams,
            )

            if isinstance(parsed, Reject):
                self.metrics.record_reject(parsed.reason)
            elif isinstance(parsed, Event):
                self.known_streams.add(parsed.stream_id)
                self.metrics.record_feature_skips(skips)
                self.engine.apply(parsed)

        # Sync metrics counters from window engine
        self.metrics.late_dropped = self.engine.late_dropped
        self.metrics.windows_flushed = self.engine.windows_flushed
        self.metrics.windows_skipped_insufficient = self.engine.windows_skipped_insufficient
        self.metrics.streams_active = len(self.known_streams)

        # Trigger checkpoint if due
        time_elapsed = now - self.last_checkpoint_ts
        if (
            self.events_since_checkpoint >= self.config.checkpoint_events
            or time_elapsed >= self.config.checkpoint_seconds
            or len(self.engine.pending_markers) > 0
        ):
            self.checkpoint(reason="due")

        # Periodically emit health line (D-12, §10.4)
        if now - self.last_health_ts >= self.config.health_seconds:
            print(self.metrics.health_line())
            self.last_health_ts = now

        return len(messages)

    def run(self) -> None:
        """Start the streaming monitor loop."""
        print(f"[etsi-watchdog] Starting StreamingMonitor on topic '{self.config.topic}'...")
        self.dispatcher.start()
        self.source.start(self.store, self.config.group)

        try:
            while not self._stop_requested:
                self.run_once()
        finally:
            self.stop()

    def stop(self) -> None:
        """Gracefully stop monitor, drain dispatcher, and close resources."""
        self._stop_requested = True
        self.checkpoint(reason="shutdown")
        self.dispatcher.stop(timeout_s=10.0)
        self.source.close()
        self.store.close()
        print("[etsi-watchdog] StreamingMonitor stopped gracefully.")
