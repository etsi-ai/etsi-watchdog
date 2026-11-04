# etsi/watchdog/streaming/source.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
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
    """

    def __init__(self, messages: Optional[List[RawMessage]] = None):
        self.messages: List[RawMessage] = list(messages or [])
        self.cursor: int = 0
        self.committed_offsets: Dict[Tuple[str, int], int] = {}
        self.is_paused: bool = False

    def add_message(self, topic: str, partition: int, offset: int, key: bytes, value: bytes) -> None:
        self.messages.append(RawMessage(topic, partition, offset, key, value))

    def start(self, store: StateStore, group: str) -> None:
        pass

    def seek(self, target_offset: int) -> None:
        """Seek cursor to first message with offset >= target_offset."""
        for idx, msg in enumerate(self.messages):
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
