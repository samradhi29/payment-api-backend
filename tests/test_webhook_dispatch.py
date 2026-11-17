
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models.outbox_event import OutboxEvent
from app.models.webhook_delivery import WebhookDelivery
from app.models.webhook_endpoint import WebhookEndpoint
from app.services.webhook_secret_service import encrypt_webhook_secret
from app.services.webhook_signing_service import verify_webhook_signature
from app.services.webhook_dispatch_service import (
    MAX_DELIVERY_ATTEMPTS,
    _retry_delay,
    dispatch_webhook_delivery,
)


@pytest.fixture
def webhook_delivery(db_session, accounts, monkeypatch):
    monkeypatch.setattr(
        "app.services.webhook_dispatch_service.validate_webhook_url",
        lambda url: url,
    )

    event = OutboxEvent(
        aggregate_type="payment",
        aggregate_id=accounts["customer"].id,
        event_type="payment.created",
        payload={
            "payment_id": "payment-test-001",
            "merchant_account_id": str(accounts["merchant"].id),
            "amount_minor": 10000,
            "currency": "INR",
            "status": "pending",
        },
        status="pending",
    )

    db_session.add(event)
    db_session.flush()

    secret = "test_webhook_secret_123456"

    endpoint = WebhookEndpoint(
        merchant_account_id=accounts["merchant"].id,
        url="https://example.com/webhooks",
        secret_encrypted=encrypt_webhook_secret(secret),
        subscribed_events=["payment.created"],
        is_active=True,
    )

    db_session.add(endpoint)
    db_session.flush()

    delivery = WebhookDelivery(
        outbox_event_id=event.id,
        webhook_endpoint_id=endpoint.id,
        status="pending",
        attempts=0,
        available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    db_session.add(delivery)
    db_session.commit()
    db_session.refresh(delivery)

    return {
        "delivery": delivery,
        "event": event,
        "endpoint": endpoint,
        "secret": secret,
    }


def test_retry_delay_is_exponential_and_capped():
    assert _retry_delay(1) == 10
    assert _retry_delay(2) == 20
    assert _retry_delay(3) == 40
    assert _retry_delay(4) == 80
    assert _retry_delay(8) == 1280
    assert _retry_delay(20) == 3600


def test_successful_webhook_delivery(db_session, webhook_delivery):
    captured = {}

    def handler(request):
        captured["body"] = request.content
        captured["headers"] = request.headers
        return httpx.Response(200, text="Webhook received")

    result = dispatch_webhook_delivery(
        db_session,
        webhook_delivery["delivery"].id,
        transport=httpx.MockTransport(handler),
    )

    assert result is True

    delivery = db_session.get(
        WebhookDelivery,
        webhook_delivery["delivery"].id,
    )

    assert delivery.status == "delivered"
    assert delivery.attempts == 1
    assert delivery.last_response_status == 200
    assert delivery.delivered_at is not None
    assert delivery.last_error is None

    timestamp = int(captured["headers"]["X-LedgerCore-Timestamp"])
    signature = captured["headers"]["X-LedgerCore-Signature"]

    assert verify_webhook_signature(
        secret=webhook_delivery["secret"],
        payload=captured["body"],
        signature_header=signature,
        timestamp=timestamp,
    )

    payload = json.loads(captured["body"])

    assert payload["type"] == "payment.created"
    assert payload["data"]["payment_id"] == "payment-test-001"


def test_http_500_schedules_retry(db_session, webhook_delivery):
    def handler(request):
        return httpx.Response(500, text="Server error")

    result = dispatch_webhook_delivery(
        db_session,
        webhook_delivery["delivery"].id,
        transport=httpx.MockTransport(handler),
    )

    assert result is False

    delivery = db_session.get(
        WebhookDelivery,
        webhook_delivery["delivery"].id,
    )

    assert delivery.status == "pending"
    assert delivery.attempts == 1
    assert delivery.last_response_status == 500
    assert delivery.last_error is not None
    assert delivery.available_at > datetime.now(timezone.utc)


def test_http_408_schedules_retry(db_session, webhook_delivery):
    def handler(request):
        return httpx.Response(408, text="Request timeout")

    result = dispatch_webhook_delivery(
        db_session,
        webhook_delivery["delivery"].id,
        transport=httpx.MockTransport(handler),
    )

    assert result is False

    delivery = db_session.get(
        WebhookDelivery,
        webhook_delivery["delivery"].id,
    )

    assert delivery.status == "pending"
    assert delivery.last_response_status == 408


def test_http_400_moves_delivery_to_dead_letter(
    db_session,
    webhook_delivery,
):
    def handler(request):
        return httpx.Response(400, text="Invalid payload")

    result = dispatch_webhook_delivery(
        db_session,
        webhook_delivery["delivery"].id,
        transport=httpx.MockTransport(handler),
    )

    assert result is False

    delivery = db_session.get(
        WebhookDelivery,
        webhook_delivery["delivery"].id,
    )

    assert delivery.status == "dead_letter"
    assert delivery.attempts == 1
    assert delivery.last_response_status == 400


def test_http_429_schedules_retry(db_session, webhook_delivery):
    def handler(request):
        return httpx.Response(429, text="Rate limited")

    result = dispatch_webhook_delivery(
        db_session,
        webhook_delivery["delivery"].id,
        transport=httpx.MockTransport(handler),
    )

    assert result is False

    delivery = db_session.get(
        WebhookDelivery,
        webhook_delivery["delivery"].id,
    )

    assert delivery.status == "pending"
    assert delivery.last_response_status == 429


def test_inactive_endpoint_moves_delivery_to_dead_letter(
    db_session,
    webhook_delivery,
):
    endpoint = webhook_delivery["endpoint"]
    endpoint.is_active = False
    db_session.commit()

    result = dispatch_webhook_delivery(
        db_session,
        webhook_delivery["delivery"].id,
    )

    assert result is False

    delivery = db_session.get(
        WebhookDelivery,
        webhook_delivery["delivery"].id,
    )

    assert delivery.status == "dead_letter"
    assert "inactive" in delivery.last_error.lower()


def test_delivery_cannot_be_claimed_twice(db_session, webhook_delivery):
    delivery_id = webhook_delivery["delivery"].id

    def handler(request):
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)

    first_result = dispatch_webhook_delivery(
        db_session,
        delivery_id,
        transport=transport,
    )

    second_result = dispatch_webhook_delivery(
        db_session,
        delivery_id,
        transport=transport,
    )

    assert first_result is True
    assert second_result is False

    delivery = db_session.get(WebhookDelivery, delivery_id)

    assert delivery.attempts == 1


def test_future_delivery_is_not_sent(db_session, webhook_delivery):
    delivery = webhook_delivery["delivery"]

    delivery.available_at = (
        datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    db_session.commit()

    result = dispatch_webhook_delivery(
        db_session,
        delivery.id,
    )

    assert result is False

    db_session.refresh(delivery)

    assert delivery.status == "pending"
    assert delivery.attempts == 0


def test_retry_waits_until_available_at(db_session, webhook_delivery):
    delivery = webhook_delivery["delivery"]

    def handler(request):
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)

    first_result = dispatch_webhook_delivery(
        db_session,
        delivery.id,
        transport=transport,
    )

    assert first_result is False

    db_session.refresh(delivery)

    assert delivery.status == "pending"
    assert delivery.attempts == 1

    # Retry is not due yet.
    second_result = dispatch_webhook_delivery(
        db_session,
        delivery.id,
        transport=transport,
    )

    assert second_result is False

    db_session.refresh(delivery)

    assert delivery.attempts == 1


def test_network_error_schedules_retry(db_session, webhook_delivery):
    def handler(request):
        raise httpx.ConnectError("Connection refused")

    result = dispatch_webhook_delivery(
        db_session,
        webhook_delivery["delivery"].id,
        transport=httpx.MockTransport(handler),
    )

    assert result is False

    delivery = db_session.get(
        WebhookDelivery,
        webhook_delivery["delivery"].id,
    )

    assert delivery.status == "pending"
    assert delivery.attempts == 1
    assert "Connection refused" in delivery.last_error


def test_maximum_attempts_moves_delivery_to_dead_letter(
    db_session,
    webhook_delivery,
):
    delivery = webhook_delivery["delivery"]
    delivery.attempts = MAX_DELIVERY_ATTEMPTS - 1
    db_session.commit()

    def handler(request):
        return httpx.Response(500, text="Server error")

    result = dispatch_webhook_delivery(
        db_session,
        delivery.id,
        transport=httpx.MockTransport(handler),
    )

    assert result is False

    db_session.refresh(delivery)

    assert delivery.status == "dead_letter"
    assert delivery.attempts == MAX_DELIVERY_ATTEMPTS
    assert delivery.last_response_status == 500
