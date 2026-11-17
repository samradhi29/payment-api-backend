
from datetime import datetime, timedelta, timezone

from app.models.outbox_event import OutboxEvent
from app.models.webhook_delivery import WebhookDelivery
from app.models.webhook_endpoint import WebhookEndpoint
from app.services.webhook_secret_service import encrypt_webhook_secret
from app.services.webhook_dispatch_service import MAX_DELIVERY_ATTEMPTS
from app.workers.outbox_worker import recover_expired_webhook_deliveries


def create_processing_delivery(db_session, accounts, attempts=1):
    event = OutboxEvent(
        aggregate_type="payment",
        aggregate_id=accounts["customer"].id,
        event_type="payment.created",
        payload={
            "payment_id": "recovery-test-payment",
            "merchant_account_id": str(accounts["merchant"].id),
            "customer_account_id": str(accounts["customer"].id),
            "amount_minor": 10000,
            "currency": "INR",
            "status": "pending",
        },
        status="published",
    )

    db_session.add(event)
    db_session.flush()

    endpoint = WebhookEndpoint(
        merchant_account_id=accounts["merchant"].id,
        url="https://example.com/webhooks",
        secret_encrypted=encrypt_webhook_secret(
            "recovery_test_secret_123456"
        ),
        subscribed_events=["payment.created"],
        is_active=True,
    )

    db_session.add(endpoint)
    db_session.flush()

    now = datetime.now(timezone.utc)

    delivery = WebhookDelivery(
        outbox_event_id=event.id,
        webhook_endpoint_id=endpoint.id,
        status="processing",
        attempts=attempts,
        available_at=now,
        processing_started_at=now - timedelta(minutes=2),
        lease_expires_at=now - timedelta(minutes=1),
    )

    db_session.add(delivery)
    db_session.commit()
    db_session.refresh(delivery)

    return delivery


def test_recover_expired_processing_delivery(db_session, accounts):
    delivery = create_processing_delivery(
        db_session,
        accounts,
        attempts=1,
    )

    result = recover_expired_webhook_deliveries(db_session)

    db_session.refresh(delivery)

    assert result["recovered"] == 1
    assert result["dead_letter"] == 0

    assert delivery.status == "pending"
    assert delivery.attempts == 1
    assert delivery.available_at <= datetime.now(timezone.utc)

    assert delivery.processing_started_at is None
    assert delivery.lease_expires_at is None
    assert delivery.claim_token is None

    assert "lease expired" in delivery.last_error.lower()


def test_expired_delivery_at_max_attempts_goes_to_dead_letter(
    db_session,
    accounts,
):
    delivery = create_processing_delivery(
        db_session,
        accounts,
        attempts=MAX_DELIVERY_ATTEMPTS,
    )

    result = recover_expired_webhook_deliveries(db_session)

    db_session.refresh(delivery)

    assert result["recovered"] == 0
    assert result["dead_letter"] == 1

    assert delivery.status == "dead_letter"
    assert delivery.attempts == MAX_DELIVERY_ATTEMPTS
    assert delivery.processing_started_at is None
    assert delivery.lease_expires_at is None
    assert delivery.claim_token is None


def test_unexpired_processing_delivery_is_not_recovered(
    db_session,
    accounts,
):
    delivery = create_processing_delivery(
        db_session,
        accounts,
        attempts=1,
    )

    future = datetime.now(timezone.utc) + timedelta(minutes=5)

    delivery.lease_expires_at = future
    db_session.commit()

    result = recover_expired_webhook_deliveries(db_session)

    db_session.refresh(delivery)

    assert result["recovered"] == 0
    assert result["dead_letter"] == 0

    assert delivery.status == "processing"
    assert delivery.lease_expires_at == future
    assert delivery.attempts == 1
