from uuid import uuid4

from app.models.outbox_event import OutboxEvent
from app.models.webhook_delivery import WebhookDelivery
from app.models.webhook_endpoint import WebhookEndpoint
from app.services.webhook_delivery_service import (
    enqueue_webhook_deliveries,
)


def test_duplicate_event_does_not_create_duplicate_deliveries(
    db_session,
    accounts,
):
    merchant = accounts["merchant"]

    endpoint = WebhookEndpoint(
        merchant_account_id=merchant.id,
        url="https://example.com/webhooks",
        secret_encrypted="test-encrypted-secret",
        subscribed_events=["payment.succeeded"],
        is_active=True,
    )

    event = OutboxEvent(
        aggregate_type="payment",
        aggregate_id=uuid4(),
        event_type="payment.succeeded",
        payload={
            "merchant_account_id": str(merchant.id),
        },
        status="pending",
    )

    db_session.add_all([endpoint, event])
    db_session.flush()

    # First enqueue should create one delivery.
    first_count = enqueue_webhook_deliveries(
        db=db_session,
        outbox_event=event,
    )

    # Reprocessing the same event should create no duplicate.
    second_count = enqueue_webhook_deliveries(
        db=db_session,
        outbox_event=event,
    )

    total_deliveries = (
        db_session.query(WebhookDelivery)
        .filter(
            WebhookDelivery.outbox_event_id == event.id,
            WebhookDelivery.webhook_endpoint_id == endpoint.id,
        )
        .count()
    )

    assert first_count == 1
    assert second_count == 0
    assert total_deliveries == 1
