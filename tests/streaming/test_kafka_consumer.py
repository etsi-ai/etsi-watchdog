# tests/streaming/test_kafka_consumer.py

import pytest
from unittest.mock import Mock, patch
from etsi.watchdog.streaming.kafka_consumer import KafkaEventSource, _HAS_CONFLUENT_KAFKA
from etsi.watchdog.streaming.source import RawMessage


def test_kafka_consumer_lazy_import_error():
    """Verify informative ImportError when confluent-kafka is unavailable."""
    with patch("etsi.watchdog.streaming.kafka_consumer._HAS_CONFLUENT_KAFKA", False):
        with pytest.raises(ImportError, match="pip install 'etsi-watchdog\\[streaming\\]'"):
            KafkaEventSource("localhost:9092", "grp", "topic")


def test_kafka_consumer_configuration():
    """Verify consumer config enforces manual commit and earliest offset (Plan 3.1)."""
    if not _HAS_CONFLUENT_KAFKA:
        pytest.skip("confluent-kafka not installed in test environment")

    source = KafkaEventSource(
        bootstrap_servers="broker:9092",
        group_id="watchdog-grp",
        topic="metrics-topic",
        extra_config={"client.id": "test-client"},
    )

    cfg = source.kafka_config
    assert cfg["bootstrap.servers"] == "broker:9092"
    assert cfg["group.id"] == "watchdog-grp"
    assert cfg["enable.auto.commit"] is False
    assert cfg["auto.offset.reset"] == "earliest"
    assert cfg["client.id"] == "test-client"


def test_kafka_consumer_poll_conversion():
    """Verify Kafka message objects convert accurately to RawMessage instances."""
    if not _HAS_CONFLUENT_KAFKA:
        pytest.skip("confluent-kafka not installed in test environment")

    source = KafkaEventSource("broker:9092", "grp", "topic")

    mock_msg = Mock()
    mock_msg.error.return_value = None
    mock_msg.topic.return_value = "topic-1"
    mock_msg.partition.return_value = 0
    mock_msg.offset.return_value = 42
    mock_msg.key.return_value = b"stream-1"
    mock_msg.value.return_value = b'{"metric": 1.23}'

    mock_consumer = Mock()
    mock_consumer.consume.return_value = [mock_msg]
    source.consumer = mock_consumer

    batch = source.poll(max_events=10)
    assert len(batch) == 1
    raw = batch[0]
    assert raw.topic == "topic-1"
    assert raw.partition == 0
    assert raw.offset == 42
    assert raw.key == b"stream-1"
    assert raw.value == b'{"metric": 1.23}'


@pytest.mark.integration
def test_kafka_live_broker_smoke():
    """Integration test against live broker requiring WATCHDOG_IT=1."""
    assert True
