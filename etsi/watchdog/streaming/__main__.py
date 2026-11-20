# etsi/watchdog/streaming/__main__.py

import signal
import sys
from typing import Optional
from .config import StreamingConfig
from .source import EventSource
from .kafka_consumer import KafkaEventSource
from .state.base import StateStore
from .state.memory_store import InMemoryStateStore
from .state.redis_store import RedisStateStore
from .state.lease import RedisOwnerLease
from .windows.keys import owner_key
from .streaming_monitor import StreamingMonitor


def main():
    print("[etsi-watchdog] Bootstrapping streaming drift watchdog...")
    try:
        config = StreamingConfig.from_env()
        config.validate()
    except Exception as e:
        print(f"[etsi-watchdog] Boot configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve StateStore backend (D-3, PR-8)
    store: StateStore
    lease: Optional[RedisOwnerLease] = None

    if config.state_backend == "memory":
        store = InMemoryStateStore()
    elif config.state_backend == "redis":
        redis_url = config.state_url or "redis://localhost:6379/0"
        o_key = owner_key(config.group)
        store = RedisStateStore(url=redis_url, owner_key=o_key)
        lease = RedisOwnerLease(
            redis_client=store._client,
            owner_key=o_key,
            lease_ttl_seconds=config.lease_ttl_seconds,
        )
    else:
        print(f"[etsi-watchdog] Unknown STATE_BACKEND: '{config.state_backend}'", file=sys.stderr)
        sys.exit(1)

    # Resolve EventSource
    source: EventSource = KafkaEventSource(
        bootstrap_servers=config.kafka_bootstrap,
        group_id=config.group,
        topic=config.topic,
    )

    monitor = StreamingMonitor(config=config, source=source, store=store, lease=lease)

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
