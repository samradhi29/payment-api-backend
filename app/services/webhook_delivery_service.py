
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.outbox_event import OutboxEvent
from app.models.webhook_delivery import WebhookDelivery
from app.models.webhook_endpoint import WebhookEndpoint


SUPPORTED_DELIVERY_EVENTS = {
    "payment.created",
    "payment.succeeded",
    "payment.failed",
}


def enqueue_webhook_deliveries(
    db: Session,
    outbox_event: OutboxEvent,
) -> int:
    """
    Create one pending delivery for each active endpoint subscribed
    to the outbox event.

    This function does not commit. The caller controls the transaction.

    The unique constraint on (outbox_event_id, webhook_endpoint_id)
    prevents duplicate delivery jobs when an event is processed again.
    """

    if outbox_event.event_type not in SUPPORTED_DELIVERY_EVENTS:
        return 0

    if outbox_event.aggregate_type != "payment":
        return 0

    payload = outbox_event.payload or {}

    merchant_account_id = payload.get("merchant_account_id")

    if not merchant_account_id:
        return 0

    try:
        merchant_id = UUID(str(merchant_account_id))
    except (ValueError, TypeError):
        return 0

    endpoints = db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.merchant_account_id == merchant_id,
            WebhookEndpoint.is_active.is_(True),
        )
    ).scalars().all()

    subscribed_endpoints = [
        endpoint
        for endpoint in endpoints
        if outbox_event.event_type in (endpoint.subscribed_events or [])
    ]

    if not subscribed_endpoints:
        return 0

    now = datetime.now(timezone.utc)

    delivery_values = [
        {
            "outbox_event_id": outbox_event.id,
            "webhook_endpoint_id": endpoint.id,
            "status": "pending",
            "attempts": 0,
            "available_at": now,
        }
        for endpoint in subscribed_endpoints
    ]

    statement = (
        insert(WebhookDelivery)
        .values(delivery_values)
        .on_conflict_do_nothing(
            index_elements=[
                "outbox_event_id",
                "webhook_endpoint_id",
            ]
        )
        .returning(WebhookDelivery.id)
    )

    result = db.execute(statement)

    return len(result.fetchall())
