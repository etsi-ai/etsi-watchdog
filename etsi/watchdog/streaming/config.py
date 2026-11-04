# etsi/watchdog/streaming/config.py

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StreamingConfig:
    """
    Streaming service configuration loaded from environment variables (D-11).
    All streaming variables are prefixed with WATCHDOG_ (except Slack variables).
    """
    reference_path: str
    features: List[str]
    topic: str
    kafka_bootstrap: str = "localhost:9092"
    group: str = "etsi-watchdog"
    algorithm: str = "psi_streaming"
    threshold: Optional[float] = None
    psi_buckets: int = 10
    window_type: str = "tumbling"
    window_seconds: float = 60.0
    hop_seconds: Optional[float] = None
    grace_seconds: float = 30.0
    min_events: int = 50
    reservoir_size: int = 2000
    reference_max_samples: int = 50000
    seed: int = 0
    state_backend: str = "memory"
    state_url: Optional[str] = None
    checkpoint_events: int = 1000
    checkpoint_seconds: float = 5.0
    lease_ttl_seconds: float = 30.0
    max_streams: int = 1000
    max_future_skew_seconds: float = 3600.0
    log_path: str = "logs/drift.csv"
    health_seconds: float = 60.0
    slack_bot_token: Optional[str] = None
    slack_channel: str = "#alerts"

    @classmethod
    def from_env(cls) -> "StreamingConfig":
        """Load configuration from environment variables."""
        ref_path = os.getenv("WATCHDOG_REFERENCE_PATH", "")
        raw_feats = os.getenv("WATCHDOG_FEATURES", "")
        features = [f.strip() for f in raw_feats.split(",") if f.strip()]
        topic = os.getenv("WATCHDOG_TOPIC", "")

        threshold_env = os.getenv("WATCHDOG_THRESHOLD")
        threshold = float(threshold_env) if threshold_env else None

        hop_env = os.getenv("WATCHDOG_HOP_SECONDS")
        hop_seconds = float(hop_env) if hop_env else None

        return cls(
            reference_path=ref_path,
            features=features,
            topic=topic,
            kafka_bootstrap=os.getenv("WATCHDOG_KAFKA_BOOTSTRAP", "localhost:9092"),
            group=os.getenv("WATCHDOG_GROUP", "etsi-watchdog"),
            algorithm=os.getenv("WATCHDOG_ALGORITHM", "psi_streaming"),
            threshold=threshold,
            psi_buckets=int(os.getenv("WATCHDOG_PSI_BUCKETS", "10")),
            window_type=os.getenv("WATCHDOG_WINDOW_TYPE", "tumbling"),
            window_seconds=float(os.getenv("WATCHDOG_WINDOW_SECONDS", "60.0")),
            hop_seconds=hop_seconds,
            grace_seconds=float(os.getenv("WATCHDOG_GRACE_SECONDS", "30.0")),
            min_events=int(os.getenv("WATCHDOG_MIN_EVENTS", "50")),
            reservoir_size=int(os.getenv("WATCHDOG_RESERVOIR_SIZE", "2000")),
            reference_max_samples=int(os.getenv("WATCHDOG_REFERENCE_MAX_SAMPLES", "50000")),
            seed=int(os.getenv("WATCHDOG_SEED", "0")),
            state_backend=os.getenv("WATCHDOG_STATE_BACKEND", "memory"),
            state_url=os.getenv("WATCHDOG_STATE_URL"),
            checkpoint_events=int(os.getenv("WATCHDOG_CHECKPOINT_EVENTS", "1000")),
            checkpoint_seconds=float(os.getenv("WATCHDOG_CHECKPOINT_SECONDS", "5.0")),
            lease_ttl_seconds=float(os.getenv("WATCHDOG_LEASE_TTL_SECONDS", "30.0")),
            max_streams=int(os.getenv("WATCHDOG_MAX_STREAMS", "1000")),
            max_future_skew_seconds=float(os.getenv("WATCHDOG_MAX_FUTURE_SKEW_SECONDS", "3600.0")),
            log_path=os.getenv("WATCHDOG_LOG_PATH", "logs/drift.csv"),
            health_seconds=float(os.getenv("WATCHDOG_HEALTH_SECONDS", "60.0")),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN"),
            slack_channel=os.getenv("SLACK_CHANNEL", "#alerts"),
        )

    def validate(self) -> None:
        """Validate configuration settings at boot; fails fast on invalid configs (D-9, D-11)."""
        if not self.reference_path:
            raise ValueError("Configuration error: WATCHDOG_REFERENCE_PATH is required.")
        if not self.features:
            raise ValueError("Configuration error: WATCHDOG_FEATURES must contain at least one feature.")
        if not self.topic:
            raise ValueError("Configuration error: WATCHDOG_TOPIC is required.")
        if self.reservoir_size > 10000:
            raise ValueError(f"Configuration error: RESERVOIR_SIZE cannot exceed 10000 (got {self.reservoir_size}).")

        # R6: log_drift raises FileNotFoundError on bare filename
        log_dir = os.path.dirname(self.log_path)
        if not log_dir:
            raise ValueError(
                f"Configuration error: WATCHDOG_LOG_PATH ('{self.log_path}') must contain a directory component "
                f"(e.g. 'logs/{self.log_path}')."
            )

        if self.algorithm == "wasserstein_streaming" and self.threshold is None:
            raise ValueError("Configuration error: Wasserstein distance threshold is required (no default).")

        if self.window_type == "hopping":
            if self.hop_seconds is None or self.hop_seconds <= 0:
                raise ValueError("Configuration error: hopping windows require HOP_SECONDS > 0.")
