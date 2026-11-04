# etsi/watchdog/streaming/__main__.py

import signal
import sys
from typing import Optional
from .config import StreamingConfig
from .source import EventSource
from .kafka_consumer import KafkaEventSource
from .state.base import StateStore
from .state.memory_store import InMemoryStateStore
from .streaming_monitor import StreamingMonitor


def main():
    print("[etsi-watchdog] Bootstrapping streaming drift watchdog...")
    try:
        config = StreamingConfig.from_env()
        config.validate()
    except Exception as e:
        print(f"[etsi-watchdog] Boot configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve StateStore backend (D-3)
    store: StateStore
    if config.state_backend == "memory":
        store = InMemoryStateStore()
    elif config.state_backend == "redis":
        # In Milestone 1, redis store placeholder or in-memory
        store = InMemoryStateStore()
    else:
        print(f"[etsi-watchdog] Unknown STATE_BACKEND: '{config.state_backend}'", file=sys.stderr)
        sys.exit(1)

    # Resolve EventSource
    source: EventSource = KafkaEventSource(
        bootstrap_servers=config.kafka_bootstrap,
        group_id=config.group,
        topic=config.topic,
    )

    monitor = StreamingMonitor(config=config, source=source, store=store)

    def _signal_handler(sig, frame):
        print("\n[etsi-watchdog] Shutdown signal received, stopping monitor...")
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        monitor.run()
    except Exception as e:
        print(f"[etsi-watchdog] Fatal error in streaming monitor: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
