# etsi/watchdog/streaming/streaming_monitor.py

import os
import time
from typing import Dict, Optional, Set, Tuple, Any
import pandas as pd
from etsi.watchdog.drift.factory import get_drift_function
from etsi.watchdog.slack_notifier import SlackNotifier
from .config import StreamingConfig
from .events import parse_and_validate, Event, Reject
from .metrics import StreamingMetrics
from .source import EventSource
from .state.base import StateStore
from .state.lease import RedisOwnerLease
from .stats.reference import ReferenceProfile
from .windows.engine import WindowEngine
from .alerts import AlertDispatcher
from .checkpoint import CheckpointWriter
from .dlq import DeadLetterSink


class StreamingMonitor:
    """
    Streaming drift monitor orchestrating Kafka consumer, window engine,
    state persistence, dead-letter queue, and alert dispatch (Task 5.1, D-12, Issue #56).
    """

    def __init__(
        self,
        config: StreamingConfig,
        source: EventSource,
        store: StateStore,
        notifier: Optional[SlackNotifier] = None,
        references: Optional[Dict[str, ReferenceProfile]] = None,
        lease: Optional[RedisOwnerLease] = None,
        dlq_sink: Optional[DeadLetterSink] = None,
    ):
        self.config: StreamingConfig = config
        self.config.validate()

        self.source: EventSource = source
        self.store: StateStore = store
        self.notifier: Optional[SlackNotifier] = notifier
        self.lease: Optional[RedisOwnerLease] = lease
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
            window_size_events=self.config.window_size_events,
        )

        # 4. Initialize Alert Dispatcher
        self.dispatcher = AlertDispatcher(
            log_path=self.config.log_path,
            notifier=self.notifier,
            metrics=self.metrics,
        )

        # 5. Initialize Atomic Checkpoint Writer (D-6)
        lease_token = self.lease.token if self.lease else getattr(self.store, "owner_token", None)
        self.checkpoint_writer = CheckpointWriter(
            group=self.config.group,
            store=self.store,
            engine=self.engine,
            dispatcher=self.dispatcher,
            lease_token=lease_token,
        )

        # 6. Initialize Dead-Letter Queue Sink (Issue #56)
        if dlq_sink is not None:
            self.dlq_sink: Optional[DeadLetterSink] = dlq_sink
        elif self.config.dead_letter_topic:
            from .dlq import KafkaDeadLetterSink
            self.dlq_sink = KafkaDeadLetterSink(
                bootstrap_servers=self.config.kafka_bootstrap,
                topic=self.config.dead_letter_topic,
            )
        else:
            self.dlq_sink = None

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
        Execute atomic Checkpoint-then-Commit across StateStore and Kafka (D-6).
        """
        if self.dlq_sink is not None:
            try:
                self.dlq_sink.flush()
            except Exception as e:
                print(f"[etsi-watchdog] Warning: Error flushing DLQ on checkpoint: {e}")

        res = self.checkpoint_writer.checkpoint(
            offsets=self.offsets,
            source=self.source,
            reason=reason,
        )
        self.events_since_checkpoint = 0
        self.last_checkpoint_ts = time.time()
        self.metrics.checkpoints += 1
        self.metrics.checkpoint_ms_last = res["elapsed_ms"]

    def _send_to_dlq(self, msg: Any, reason: str, error_message: str) -> None:
        if self.dlq_sink is None:
            return
        try:
            self.dlq_sink.publish(raw_message=msg, reason=reason, error_message=error_message)
            self.metrics.dead_letter_published += 1
        except Exception as e:
            self.metrics.dead_letter_errors += 1
            print(f"[etsi-watchdog] Warning: Failed to publish message to DLQ: {e}")

    def run_once(self, max_events: int = 500, timeout_s: float = 1.0) -> int:
        """Execute a single poll -> validate -> apply -> checkpoint cycle."""
        # Renew lease if due
        if self.lease is not None:
            self.lease.renew_if_due()

        messages = self.source.poll(max_events=max_events, timeout_s=timeout_s)
        now = time.time()

        for msg in messages:
            self.metrics.events_in += 1
            self.events_since_checkpoint += 1
            self.offsets[(msg.topic, msg.partition)] = msg.offset + 1

            if not msg.value:
                self.metrics.record_reject("bad_json")
                if self.dlq_sink is not None:
                    self._send_to_dlq(msg, "bad_json", "Empty message payload")
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
                if self.dlq_sink is not None:
                    self._send_to_dlq(msg, parsed.reason, parsed.message)
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

        if self.lease is not None:
            print(f"[etsi-watchdog] Acquiring owner lease for key '{self.lease.owner_key}'...")
            self.lease.acquire()
            print(f"[etsi-watchdog] Owner lease acquired successfully (token: {self.lease.token}).")

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
        try:
            self.checkpoint(reason="shutdown")
        except Exception as e:
            print(f"[etsi-watchdog] Warning: error during final checkpoint: {e}")

        self.dispatcher.stop(timeout_s=10.0)
        self.source.close()

        if self.dlq_sink is not None:
            try:
                self.dlq_sink.close()
            except Exception as e:
                print(f"[etsi-watchdog] Warning: failed to close DLQ sink: {e}")

        if self.lease is not None:
            try:
                self.lease.release()
            except Exception as e:
                print(f"[etsi-watchdog] Warning: failed to release owner lease: {e}")

        self.store.close()
        print("[etsi-watchdog] StreamingMonitor stopped gracefully.")
