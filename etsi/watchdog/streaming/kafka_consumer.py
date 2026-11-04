# etsi/watchdog/streaming/kafka_consumer.py

from typing import Optional, List, Dict, Tuple, Any, Callable
from .source import EventSource, RawMessage
from .state.base import StateStore

try:
    import confluent_kafka
    from confluent_kafka import Consumer, TopicPartition, KafkaError
    _HAS_CONFLUENT_KAFKA = True
except ImportError:
    _HAS_CONFLUENT_KAFKA = False
    Consumer = None
    TopicPartition = None
    KafkaError = None


class KafkaEventSource(EventSource):
    """
    Kafka message source using confluent-kafka (Plan 3.1).
    Enforces manual offset commit (enable.auto.commit=False), auto.offset.reset=earliest,
    and batch message consumption via consume().
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        topic: str,
        on_assign_cb: Optional[Callable[[Any, List[Any]], None]] = None,
        on_revoke_cb: Optional[Callable[[Any, List[Any]], None]] = None,
        extra_config: Optional[Dict[str, Any]] = None,
    ):
        if not _HAS_CONFLUENT_KAFKA:
            raise ImportError(
                "confluent-kafka is required for Kafka streaming. "
                "Install via: pip install 'etsi-watchdog[streaming]'"
            )

        self.bootstrap_servers: str = bootstrap_servers
        self.group_id: str = group_id
        self.topic: str = topic
        self.on_assign_cb = on_assign_cb
        self.on_revoke_cb = on_revoke_cb

        self.kafka_config: Dict[str, Any] = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "session.timeout.ms": 30000,
            "max.poll.interval.ms": 300000,
        }
        if extra_config:
            self.kafka_config.update(extra_config)

        self.consumer: Optional[Any] = None
        self._is_paused: bool = False

    def start(self, store: StateStore, group: str) -> None:
        """Instantiate consumer and subscribe to target topic."""
        self.consumer = Consumer(self.kafka_config)

        def _on_assign(c, partitions):
            if self.on_assign_cb:
                self.on_assign_cb(c, partitions)
            else:
                c.assign(partitions)

        def _on_revoke(c, partitions):
            if self.on_revoke_cb:
                self.on_revoke_cb(c, partitions)

        self.consumer.subscribe([self.topic], on_assign=_on_assign, on_revoke=_on_revoke)

    def poll(self, max_events: int = 500, timeout_s: float = 1.0) -> List[RawMessage]:
        if not self.consumer or self._is_paused:
            return []

        msgs = self.consumer.consume(num_messages=max_events, timeout=timeout_s)
        batch: List[RawMessage] = []

        for msg in msgs:
            err = msg.error()
            if err:
                if err.code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    print(f"[etsi-watchdog] Kafka consumer error: {err}")
                    continue

            batch.append(
                RawMessage(
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                    key=msg.key(),
                    value=msg.value(),
                )
            )

        return batch

    def commit(self, offsets: Dict[Tuple[str, int], int]) -> None:
        """Advisory asynchronous offset commit to Kafka."""
        if not self.consumer or not offsets:
            return

        topic_partitions = [
            TopicPartition(topic=t, partition=p, offset=nxt)
            for (t, p), nxt in offsets.items()
        ]

        def _commit_cb(err, partitions):
            if err:
                print(f"[etsi-watchdog] Warning: Advisory Kafka offset commit failed: {err}")

        try:
            self.consumer.commit(offsets=topic_partitions, asynchronous=True)
        except Exception as e:
            print(f"[etsi-watchdog] Exception during advisory commit: {e}")

    def pause(self) -> None:
        self._is_paused = True
        if self.consumer:
            assigned = self.consumer.assignment()
            if assigned:
                self.consumer.pause(assigned)

    def resume(self) -> None:
        self._is_paused = False
        if self.consumer:
            assigned = self.consumer.assignment()
            if assigned:
                self.consumer.resume(assigned)

    def close(self) -> None:
        if self.consumer:
            try:
                self.consumer.close()
            except Exception:
                pass
            self.consumer = None
