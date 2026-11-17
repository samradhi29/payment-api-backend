import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    redis_url: str = os.getenv(
        "METRICS_REDIS_URL",
        "redis://localhost:6379/1",
    )
    kafka_bootstrap_servers: str = os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS",
        "localhost:9092",
    )
    kafka_topic: str = os.getenv(
        "KAFKA_METRICS_TOPIC",
        "payment.attempt.events",
    )
    kafka_group_id: str = os.getenv(
        "KAFKA_METRICS_GROUP",
        "payment-metrics-consumer",
    )
    database_url: str = os.getenv(
        "METRICS_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/payment_metrics",
    )
    snapshot_interval_seconds: int = 60


settings = Settings()
