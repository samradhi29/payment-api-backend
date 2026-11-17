
import math
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.outbox_event import OutboxEvent


def enqueue_gateway_attempt(
    db: Session,
    *,
    payment_id: UUID | str,
    gateway: str,
    outcome: str,
    latency_ms: float,
    payment_method: str | None = None,
    bank_code: str | None = None,
    amount_minor: int | None = None,
    currency: str | None = None,
    error: str | None = None,
) -> OutboxEvent:
    """
    Add a gateway attempt telemetry event to the transactional outbox.

    The caller controls the database transaction and commit.
    No Kafka request is made here.
    """

    normalized_payment_id = UUID(str(payment_id))

    gateway = gateway.strip().lower()
    outcome = outcome.strip().lower()

    if not gateway:
        raise ValueError("Gateway cannot be empty")

    if outcome not in {"success", "failure", "timeout"}:
        raise ValueError("Invalid gateway outcome")

    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, (int, float))
        or not math.isfinite(latency_ms)
        or latency_ms < 0
    ):
        raise ValueError(
            "Latency must be finite and non-negative"
        )

    if amount_minor is not None:
        if (
            isinstance(amount_minor, bool)
            or not isinstance(amount_minor, int)
            or amount_minor < 0
        ):
            raise ValueError("amount_minor must be a non-negative integer")

    payload = {
        "payment_id": str(normalized_payment_id),
        "gateway": gateway,
        "outcome": outcome,
        "latency_ms": round(float(latency_ms), 2),
        "payment_method": payment_method or "UNKNOWN",
        "bank_code": bank_code or "UNKNOWN",
        "amount_minor": amount_minor,
        "currency": currency,
        "error": error,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }

    event = OutboxEvent(
        aggregate_type="payment",
        aggregate_id=normalized_payment_id,
        event_type="payment.gateway.attempt",
        payload=payload,
        status="pending",
    )

    db.add(event)

    return event
