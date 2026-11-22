# etsi/watchdog/streaming/source.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Callable, Any
from etsi.watchdog.streaming.state.base import StateStore


@dataclass
class RawMessage:
    topic: str
    partition: int
    offset: int
    key: Optional[bytes]
    value: Optional[bytes]


class EventSource(ABC):
    """
    Abstract message source interface separating Kafka I/O from streaming engine (D-12).
    Enables broker-free unit and integration testing via FakeEventSource.
    """

    @abstractmethod
    def start(self, store: StateStore, group: str) -> None:
        """Start the source and seek offsets from state store if applicable."""
        pass

    @abstractmethod
    def poll(self, max_events: int = 500, timeout_s: float = 1.0) -> List[RawMessage]:
        """Poll a batch of messages."""
        pass

    @abstractmethod
    def commit(self, offsets: Dict[Tuple[str, int], int]) -> None:
        """Advisory offset commit."""
        pass

    @abstractmethod
    def pause(self) -> None:
        pass

    @abstractmethod
    def resume(self) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass


class FakeEventSource(EventSource):
    """
    In-memory message source for tests, simulating a partition log.
    Allows seeking, pausing, and tracking advisory commits.
    Supports partition assignment/revocation simulation for rebalance testing.
    """

    def __init__(
        self,
        messages: Optional[List[RawMessage]] = None,
        on_assign_cb: Optional[Callable[[Any, List[Any]], None]] = None,
        on_revoke_cb: Optional[Callable[[Any, List[Any]], None]] = None,
    ):
        self.messages: List[RawMessage] = list(messages or [])
        self.cursor: int = 0
        self.committed_offsets: Dict[Tuple[str, int], int] = {}
        self.is_paused: bool = False
        self.on_assign_cb = on_assign_cb
        self.on_revoke_cb = on_revoke_cb
        self.assigned_partitions: List[Any] = []

    def add_message(self, topic: str, partition: int, offset: int, key: bytes, value: bytes) -> None:
        self.messages.append(RawMessage(topic, partition, offset, key, value))

    def start(self, store: StateStore, group: str) -> None:
        if self.assigned_partitions and self.on_assign_cb:
            self.on_assign_cb(self, self.assigned_partitions)

    def trigger_rebalance(self, revoked: List[Any], assigned: List[Any]) -> None:
        """Simulate rebalance revocation and assignment callbacks (Task 3.4)."""
        if self.on_revoke_cb and revoked:
            self.on_revoke_cb(self, revoked)
        self.assigned_partitions = [p for p in self.assigned_partitions if p not in revoked]
        if assigned:
            self.assigned_partitions.extend(assigned)
            if self.on_assign_cb:
                self.on_assign_cb(self, assigned)

    def seek(self, target_offset: int, topic: Optional[str] = None, partition: Optional[int] = None) -> None:
        """Seek cursor to first message with offset >= target_offset."""
        for idx, msg in enumerate(self.messages):
            if (topic is None or msg.topic == topic) and (partition is None or msg.partition == partition):
                if msg.offset >= target_offset:
                    self.cursor = idx
                    return
        self.cursor = len(self.messages)

    def poll(self, max_events: int = 500, timeout_s: float = 1.0) -> List[RawMessage]:
        if self.is_paused:
            return []
        batch = self.messages[self.cursor : self.cursor + max_events]
        self.cursor += len(batch)
        return batch

    def commit(self, offsets: Dict[Tuple[str, int], int]) -> None:
        self.committed_offsets.update(offsets)

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def close(self) -> None:
        pass
