import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)


class KafkaAttemptPublisher:
    def __init__(
        self,
        bootstrap_servers: str,
        topic: str = "payment.attempt.events",
    ):
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.producer = None

    async def start(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda value: json.dumps(value).encode("utf-8"),
        )
        await self.producer.start()

    async def stop(self):
        if self.producer is not None:
            await self.producer.stop()
            self.producer = None

    async def publish(
        self,
        *,
        payment_id: str,
        gateway: str,
        payment_method: str | None,
        bank_code: str | None,
        amount_minor: int,
        currency: str,
        outcome: str,
        latency_ms: float,
        error: str | None = None,
    ):
        if self.producer is None:
            return

        event = {
            "event_id": str(uuid4()),
            "event_type": "payment.gateway.attempt",
            "payment_id": payment_id,
            "gateway": gateway,
            "payment_method": payment_method or "UNKNOWN",
            "bank_code": bank_code or "UNKNOWN",
            "amount_minor": amount_minor,
            "currency": currency,
            "outcome": outcome,
            "latency_ms": round(latency_ms, 2),
            "error": error,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await asyncio.wait_for(
                self.producer.send_and_wait(self.topic, event),
                timeout=1.0,
            )
        except Exception:
            logger.exception(
                "Failed to publish gateway attempt event for %s",
                payment_id,
            )
