# etsi/watchdog/streaming/checkpoint.py

import time
from typing import Dict, List, Optional, Tuple, Any
from etsi.watchdog.streaming.state.base import StateStore
from etsi.watchdog.streaming.state.codec import encode
from etsi.watchdog.streaming.windows.keys import offset_key, wm_key, stream_count_key, win_key, marker_key
from etsi.watchdog.streaming.windows.engine import WindowEngine, Marker
from etsi.watchdog.streaming.alerts import AlertDispatcher


class CheckpointWriter:
    """
    Implements atomic Checkpoint-then-Commit pattern (Task 3.2, D-6).
    Guarantees that state persistence, watermark progress, alert markers,
    and Kafka offsets are committed together before any message offsets
    are committed to the broker or alerts dispatched downstream.
    """

    def __init__(
        self,
        group: str,
        store: StateStore,
        engine: WindowEngine,
        dispatcher: AlertDispatcher,
        lease_token: Optional[str] = None,
        ttl_margin_seconds: int = 300,
        marker_ttl_seconds: int = 86400,
    ):
        self.group: str = str(group)
        self.store: StateStore = store
        self.engine: WindowEngine = engine
        self.dispatcher: AlertDispatcher = dispatcher
        self.lease_token: Optional[str] = lease_token
        self.ttl_margin_seconds: int = int(ttl_margin_seconds)
        self.marker_ttl_seconds: int = int(marker_ttl_seconds)

    def checkpoint(
        self,
        offsets: Dict[Tuple[str, int], int],
        source: Optional[Any] = None,
        reason: str = "scheduled",
    ) -> Dict[str, Any]:
        """
        Executes atomic checkpoint across state store, then triggers advisory Kafka commit
        and marker dispatch.

        Raises:
            StateStoreError / FenceMismatchError: if durable persistence fails.
        """
        t0 = time.perf_counter()
        puts: Dict[str, bytes] = {}
        dels: List[str] = []
        ttl_map: Dict[str, int] = {}

        # 1. Open dirty windows (dirty minus flushed)
        open_dirty_keys = self.engine.dirty - self.engine.flushed
        for stream_id, feature, ws in open_dirty_keys:
            key_tuple = (stream_id, feature, ws)
            if key_tuple in self.engine.open_windows:
                stat = self.engine.open_windows[key_tuple]
                k = win_key(self.group, stream_id, feature, ws)
                payload = {
                    "schema_version": 1,
                    "algo": self.engine.algo,
                    "state": stat.to_state(),
                }
                puts[k] = encode(payload)
                # TTL = window_size + grace + margin (D-3)
                window_ttl = (
                    int(self.ttl_margin_seconds * 2)
                    if getattr(self.engine, "is_count_based", False)
                    else int(self.engine.window_size + self.engine.grace + self.ttl_margin_seconds)
                )
                ttl_map[k] = window_ttl

        # 2. Watermarks or counts for dirty streams
        for stream_id in self.engine.dirty_streams:
            if getattr(self.engine, "is_count_based", False):
                if stream_id in self.engine.stream_counts:
                    k = stream_count_key(self.group, stream_id)
                    puts[k] = encode(int(self.engine.stream_counts[stream_id]))
            else:
                if stream_id in self.engine.watermarks:
                    k = wm_key(self.group, stream_id)
                    puts[k] = encode(float(self.engine.watermarks[stream_id]))

        # 3. Flushed windows to delete from active state
        for stream_id, feature, ws in self.engine.flushed:
            k = win_key(self.group, stream_id, feature, ws)
            dels.append(k)

        # 4. Flushed window alert markers (persisted in pending state)
        new_markers: List[Marker] = list(self.engine.pending_markers)
        for m in new_markers:
            k = marker_key(self.group, m.stream_id, m.feature, m.algo, m.window_start)
            payload = {
                "schema_version": 1,
                "status": "pending",
                "idempotency_key": m.idempotency_key,
                "result": m.result.to_json(),
            }
            puts[k] = encode(payload)
            ttl_map[k] = self.marker_ttl_seconds

        # 5. Kafka partition next offsets
        for (topic, partition), next_offset in offsets.items():
            k = offset_key(self.group, topic, partition)
            puts[k] = encode(int(next_offset))

        # 6. ATOMIC WRITE TO STATE STORE
        # Crucial ordering (D-6): store write MUST succeed before offsets or markers escape.
        self.store.write_batch(
            puts=puts,
            deletes=dels,
            ttl=ttl_map,
            fence=self.lease_token,
        )

        # 7. Clear engine tracking sets after verified durable persistence
        self.engine.dirty.clear()
        self.engine.dirty_streams.clear()
        self.engine.flushed.clear()
        self.engine.pending_markers.clear()

        # 8. Advisory commit to source (Kafka)
        if source is not None and offsets:
            try:
                source.commit(offsets)
            except Exception:
                # Failure to commit to Kafka is advisory; state store is ground truth
                pass

        # 9. Hand durable markers to alert dispatcher ONLY after durable write
        if new_markers:
            self.dispatcher.enqueue(new_markers)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "reason": reason,
            "puts": len(puts),
            "deletes": len(dels),
            "markers": len(new_markers),
            "offsets": len(offsets),
            "elapsed_ms": elapsed_ms,
        }
