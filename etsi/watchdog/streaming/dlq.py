# etsi/watchdog/streaming/dlq.py

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from .source import RawMessage

try:
    import confluent_kafka
    from confluent_kafka import Producer
    _HAS_CONFLUENT_KAFKA = True
except ImportError:
    _HAS_CONFLUENT_KAFKA = False
    Producer = None


@dataclass
class DeadLetterRecord:
    """
    Structured envelope for messages published to a Dead-Letter Queue (Issue #56).
    Preserves original message coordinates, the raw payload, and the reject diagnostic.
    """
    reason: str
    error_message: str
    timestamp: float
    original_topic: str
    original_partition: int
    original_offset: int
    original_key: Optional[str] = None
    payload: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


class DeadLetterSink(ABC):
    """
    Abstract interface for publishing rejected/poison messages to a DLQ.
    Separates Kafka transport from streaming monitor logic to enable broker-free testing.
    """

    @abstractmethod
    def publish(
        self,
        raw_message: RawMessage,
        reason: str,
        error_message: str,
    ) -> None:
        """Publish a rejected message and its diagnostic to the dead-letter sink."""
        pass

    @abstractmethod
    def flush(self, timeout_s: float = 5.0) -> None:
        """Flush any buffered messages to the sink."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the sink and release underlying resources."""
        pass


class FakeDeadLetterSink(DeadLetterSink):
    """
    In-memory dead-letter sink for unit and integration testing without a Kafka broker.
    Captures DeadLetterRecords and allows simulated publisher errors.
    """

    def __init__(self, simulate_failure: bool = False):
        self.records: List[DeadLetterRecord] = []
        self.simulate_failure: bool = simulate_failure
        self.is_flushed: bool = False
        self.is_closed: bool = False

    def publish(
        self,
        raw_message: RawMessage,
        reason: str,
        error_message: str,
    ) -> None:
        if self.simulate_failure:
            raise RuntimeError("Simulated DLQ publisher failure")

        key_str = raw_message.key.decode("utf-8", errors="replace") if raw_message.key else None
        payload_str = raw_message.value.decode("utf-8", errors="replace") if raw_message.value else None

        record = DeadLetterRecord(
            reason=reason,
            error_message=error_message,
            timestamp=time.time(),
            original_topic=raw_message.topic,
            original_partition=raw_message.partition,
            original_offset=raw_message.offset,
            original_key=key_str,
            payload=payload_str,
        )
        self.records.append(record)

    def flush(self, timeout_s: float = 5.0) -> None:
        self.is_flushed = True

    def close(self) -> None:
        self.is_closed = True


class KafkaDeadLetterSink(DeadLetterSink):
    """
    Kafka dead-letter topic publisher using confluent-kafka Producer.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        extra_config: Optional[Dict[str, Any]] = None,
    ):
        if not _HAS_CONFLUENT_KAFKA:
            raise ImportError(
                "confluent-kafka is required for Kafka dead-letter queue. "
                "Install via: pip install 'etsi-watchdog[streaming]'"
            )

        self.bootstrap_servers = bootstrap_servers
        self.topic = topic

        producer_config: Dict[str, Any] = {
            "bootstrap.servers": self.bootstrap_servers,
            "acks": "all",
            "retries": 3,
            "linger.ms": 5,
        }
        if extra_config:
            producer_config.update(extra_config)

        self.producer: Optional[Any] = Producer(producer_config)

    def publish(
        self,
        raw_message: RawMessage,
        reason: str,
        error_message: str,
    ) -> None:
        if not self.producer:
            return

        key_str = raw_message.key.decode("utf-8", errors="replace") if raw_message.key else None
        payload_str = raw_message.value.decode("utf-8", errors="replace") if raw_message.value else None

        record = DeadLetterRecord(
            reason=reason,
            error_message=error_message,
            timestamp=time.time(),
            original_topic=raw_message.topic,
            original_partition=raw_message.partition,
            original_offset=raw_message.offset,
            original_key=key_str,
            payload=payload_str,
        )

        dlq_payload = record.to_bytes()
        dlq_key = raw_message.key if raw_message.key is not None else reason.encode("utf-8")

        self.producer.produce(
            topic=self.topic,
            key=dlq_key,
            value=dlq_payload,
        )
        self.producer.poll(0)

    def flush(self, timeout_s: float = 5.0) -> None:
        if self.producer:
            self.producer.flush(timeout=timeout_s)

    def close(self) -> None:
        if self.producer:
            try:
                self.producer.flush(timeout=5.0)
            except Exception:
                pass
            self.producer = None
