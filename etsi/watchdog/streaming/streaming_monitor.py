# etsi/watchdog/streaming/streaming_monitor.py

import os
import time
from typing import Dict, Optional, Set, Tuple, Any, List
import pandas as pd
from etsi.watchdog.drift.base import DriftResult
from etsi.watchdog.drift.factory import get_drift_function
from etsi.watchdog.slack_notifier import SlackNotifier
from .config import StreamingConfig
from .events import parse_and_validate, Event, Reject
from .metrics import StreamingMetrics
from .source import EventSource
from .state.base import StateStore
from .state.codec import decode
from .state.lease import RedisOwnerLease
from .stats.base import StreamingStat
from .stats.reference import ReferenceProfile
from .windows.engine import WindowEngine, Marker
from .windows.keys import offset_key, parse_win_key
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
        self.assigned_partitions: Set[Tuple[str, int]] = set()
        self.stream_partitions: Dict[str, Tuple[str, int]] = {}
        self.events_since_checkpoint: int = 0
        self.last_checkpoint_ts: float = time.time()
        self.last_health_ts: float = time.time()
        self._stop_requested: bool = False

        # Wire rebalance callbacks if source supports them (Task 3.4, Issue #51)
        if hasattr(self.source, "on_assign_cb") and getattr(self.source, "on_assign_cb") is None:
            self.source.on_assign_cb = self._on_assign
        if hasattr(self.source, "on_revoke_cb") and getattr(self.source, "on_revoke_cb") is None:
            self.source.on_revoke_cb = self._on_revoke

    def _stat_from_state(self, algo: str, state_dict: Dict[str, Any]) -> StreamingStat:
        algo_func = get_drift_function(algo)
        stat_cls = getattr(algo_func, "stat_factory", None)
        if stat_cls is not None and hasattr(stat_cls, "from_state"):
            return stat_cls.from_state(state_dict)
        raise ValueError(f"Cannot rehydrate stat for algorithm '{algo}'")

    def rehydrate(self) -> int:
        """
        Rehydrate open window states, monotonic stream watermarks/counts, and pending markers
        from durable StateStore (Task 3.3, Task 3.4, Issue #51).
        Ensures a newly assigned consumer instance resumes without losing or double-counting state.
        Returns the number of rehydrated open window slices.
        """
        rehydrated_count = 0
        group = self.config.group

        # 1. Rehydrate Watermarks
        wm_prefix = f"wd:v1:{group}:wm:"
        try:
            wm_keys = self.store.list_prefix(wm_prefix)
            for k in wm_keys:
                stream_id = k[len(wm_prefix):]
                raw = self.store.get(k)
                if raw is not None:
                    self.engine.watermarks[stream_id] = float(decode(raw))
                    self.known_streams.add(stream_id)
        except Exception as e:
            print(f"[etsi-watchdog] Warning: Error rehydrating watermarks: {e}")

        # 2. Rehydrate Stream Counts (for count-based windows)
        count_prefix = f"wd:v1:{group}:count:"
        try:
            count_keys = self.store.list_prefix(count_prefix)
            for k in count_keys:
                stream_id = k[len(count_prefix):]
                raw = self.store.get(k)
                if raw is not None:
                    self.engine.stream_counts[stream_id] = int(decode(raw))
                    self.known_streams.add(stream_id)
        except Exception as e:
            print(f"[etsi-watchdog] Warning: Error rehydrating stream counts: {e}")

        # 3. Rehydrate Open Window States
        win_prefix = f"wd:v1:{group}:win:"
        try:
            win_keys = self.store.list_prefix(win_prefix)
            for k in win_keys:
                raw = self.store.get(k)
                if raw is None:
                    continue
                try:
                    parsed = parse_win_key(k)
                except Exception:
                    continue

                payload = decode(raw)
                algo = payload.get("algo", self.config.algorithm)
                state_dict = payload.get("state")
                if not state_dict:
                    continue

                stat = self._stat_from_state(algo, state_dict)
                stream_id = parsed["stream_id"]
                feature = parsed["feature"]
                ws = parsed["window_start"]
                engine_ws = f"c:{ws}" if parsed["window_type"] == "count" else float(ws)

                key_tuple = (stream_id, feature, engine_ws)
                self.engine.open_windows[key_tuple] = stat
                self.known_streams.add(stream_id)
                rehydrated_count += 1
        except Exception as e:
            print(f"[etsi-watchdog] Warning: Error rehydrating window states: {e}")

        # 4. Rehydrate and Re-dispatch Pending Markers
        marker_prefix = f"wd:v1:{group}:marker:"
        try:
            marker_keys = self.store.list_prefix(marker_prefix)
            for k in marker_keys:
                raw = self.store.get(k)
                if raw is None:
                    continue
                m_payload = decode(raw)
                if m_payload.get("status") == "pending":
                    marker_dict = m_payload.get("marker", {})
                    res_dict = marker_dict.get("result", {})
                    res = DriftResult(
                        method=res_dict.get("method", self.config.algorithm),
                        score=float(res_dict.get("score", 0.0)),
                        threshold=float(res_dict.get("threshold", self.threshold)),
                        sample_size=int(res_dict.get("sample_size", 0)),
                        details=res_dict.get("details", {}),
                    )
                    marker = Marker(
                        stream_id=marker_dict.get("stream_id", ""),
                        feature=marker_dict.get("feature", ""),
                        algo=marker_dict.get("algo", self.config.algorithm),
                        window_start=marker_dict.get("window_start", 0),
                        window_end=marker_dict.get("window_end", 0),
                        result=res,
                    )
                    self.dispatcher.enqueue([marker])
        except Exception as e:
            print(f"[etsi-watchdog] Warning: Error re-dispatching pending markers: {e}")

        # 5. Sweep flush_due across active streams as a safety net
        if not self.engine.is_count_based:
            for s in list(self.engine.watermarks.keys()):
                self.engine.flush_due(s)
            if self.engine.pending_markers:
                self.checkpoint(reason="rehydrate_flush")

        return rehydrated_count

    def _on_assign(self, consumer_or_source: Any, partitions: List[Any]) -> None:
        """
        Partition assignment handler (Task 3.4, Issue #51).
        Invoked by Kafka consumer or EventSource when partitions are assigned.
        Reads durable offsets from StateStore and seeks each partition.
        Then rehydrates open window states from StateStore.
        """
        for p in partitions:
            topic = getattr(p, "topic", None)
            partition = getattr(p, "partition", None)
            if topic is None and isinstance(p, (tuple, list)) and len(p) >= 2:
                topic, partition = p[0], p[1]
            if topic is None:
                topic = self.config.topic
            if partition is None and isinstance(p, int):
                partition = p

            if topic is not None and partition is not None:
                tp = (topic, int(partition))
                self.assigned_partitions.add(tp)
                k = offset_key(self.config.group, topic, int(partition))
                stored = self.store.get(k)
                if stored is not None:
                    stored_offset = int(decode(stored))
                    self.offsets[tp] = stored_offset
                    if hasattr(consumer_or_source, "seek"):
                        try:
                            if hasattr(consumer_or_source, "assignment"):
                                from confluent_kafka import TopicPartition
                                consumer_or_source.seek(TopicPartition(topic, int(partition), stored_offset))
                            else:
                                consumer_or_source.seek(stored_offset, topic=topic, partition=int(partition))
                        except Exception as e:
                            print(f"[etsi-watchdog] Warning: Failed to seek partition {tp} to {stored_offset}: {e}")

        n_rehydrated = self.rehydrate()
        print(f"[etsi-watchdog] Rebalance on_assign: assigned {len(partitions)} partition(s), rehydrated {n_rehydrated} window(s).")

    def _on_revoke(self, consumer_or_source: Any, partitions: List[Any]) -> None:
        """
        Partition revocation handler (Task 3.4, Issue #51).
        Invoked when partitions are revoked during rebalance.
        Persists all in-memory window state and offsets to StateStore before handoff,
        then cleans up local in-memory state for revoked streams.
        """
        print(f"[etsi-watchdog] Rebalance on_revoke: revoking {len(partitions)} partition(s), persisting state...")
        try:
            self.checkpoint(reason="rebalance_revoke")
        except Exception as e:
            print(f"[etsi-watchdog] Error persisting state during partition revocation: {e}")

        revoked_tps = set()
        for p in partitions:
            topic = getattr(p, "topic", None)
            partition = getattr(p, "partition", None)
            if topic is None and isinstance(p, (tuple, list)) and len(p) >= 2:
                topic, partition = p[0], p[1]
            if topic is None:
                topic = self.config.topic
            if partition is None and isinstance(p, int):
                partition = p
            if topic is not None and partition is not None:
                revoked_tps.add((topic, int(partition)))

        for tp in revoked_tps:
            self.assigned_partitions.discard(tp)

        revoked_streams = {
            s for s, tp in list(self.stream_partitions.items())
            if tp in revoked_tps
        }
        for s in revoked_streams:
            self.engine.clear_stream(s)
            self.known_streams.discard(s)
            self.stream_partitions.pop(s, None)

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
                self.stream_partitions[parsed.stream_id] = (msg.topic, msg.partition)
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
        self.rehydrate()
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
