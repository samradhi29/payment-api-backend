
from sqlalchemy import select, func

from app.models.payment import Payment
from app.models.idempotency_key import IdempotencyKey
from app.models.outbox_event import OutboxEvent
from app.services.merchant_api_key_service import (
    create_merchant_api_key,
)


def create_payload(accounts, amount=10000):
    return {
        "customer_account_id": str(accounts["customer"].id),
        "amount_minor": amount,
        "currency": "INR",
    }


def test_same_key_same_request_returns_original_payment(
    client,
    accounts,
    db_session,
):
    payload = create_payload(accounts)

    headers = {
        "Idempotency-Key": "duplicate-payment-001"
    }

    first_response = client.post(
        "/api/v1/payments",
        json=payload,
        headers=headers,
    )

    second_response = client.post(
        "/api/v1/payments",
        json=payload,
        headers=headers,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    assert first_response.json()["id"] == second_response.json()["id"]

    payment_count = db_session.scalar(
        select(func.count()).select_from(Payment)
    )

    assert payment_count == 1

    event_count = db_session.scalar(
        select(func.count()).select_from(OutboxEvent)
    )

    assert event_count == 1

    key_count = db_session.scalar(
        select(func.count()).select_from(IdempotencyKey)
    )

    assert key_count == 1


def test_same_key_different_request_returns_conflict(
    client,
    accounts,
    db_session,
):
    headers = {
        "Idempotency-Key": "conflicting-payment-001"
    }

    first_response = client.post(
        "/api/v1/payments",
        json=create_payload(accounts, 10000),
        headers=headers,
    )

    second_response = client.post(
        "/api/v1/payments",
        json=create_payload(accounts, 20000),
        headers=headers,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409

    assert (
        second_response.json()["detail"]
        == "Idempotency key already used with a different request"
    )

    payment_count = db_session.scalar(
        select(func.count()).select_from(Payment)
    )

    assert payment_count == 1


def test_different_keys_create_different_payments(
    client,
    accounts,
    db_session,
):
    payload = create_payload(accounts)

    first_response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": "unique-payment-001"},
    )

    second_response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": "unique-payment-002"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    assert first_response.json()["id"] != second_response.json()["id"]

    payment_count = db_session.scalar(
        select(func.count()).select_from(Payment)
    )

    assert payment_count == 2


def test_idempotency_key_is_scoped_to_merchant(
    client,
    accounts,
    account_factory,
    db_session,
):
    # Create a second merchant.
    second_merchant = account_factory(
        account_number="TEST-MERCHANT-002",
        name="Second Merchant",
    )

    # Generate a separate API key for the second merchant.
    _, second_merchant_api_key = create_merchant_api_key(
        db=db_session,
        account_id=second_merchant.id,
        label="Second Merchant Test Key",
    )

    payload = create_payload(accounts)

    # First merchant uses the shared idempotency key.
    first_response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={
            "Idempotency-Key": "shared-key-001",
        },
    )

    # Second merchant uses the same key independently.
    second_response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={
            "Idempotency-Key": "shared-key-001",
            "X-Merchant-API-Key": second_merchant_api_key,
        },
    )

    assert first_response.status_code == 201, first_response.json()
    assert second_response.status_code == 201, second_response.json()

    first_payment = first_response.json()
    second_payment = second_response.json()

    # Each merchant must create a separate payment.
    assert first_payment["id"] != second_payment["id"]

    assert first_payment["merchant_account_id"] == str(
        accounts["merchant"].id
    )

    assert second_payment["merchant_account_id"] == str(
        second_merchant.id
    )

    # Both payments must be stored.
    payment_count = db_session.scalar(
        select(func.count()).select_from(Payment)
    )

    assert payment_count == 2

    # Each merchant must have its own idempotency record.
    key_count = db_session.scalar(
        select(func.count()).select_from(IdempotencyKey)
    )

    assert key_count == 2
