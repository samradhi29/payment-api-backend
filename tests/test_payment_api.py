
from sqlalchemy import select, func

from app.models.payment import Payment
from app.models.outbox_event import OutboxEvent


def payment_payload(accounts):
    return {
        "customer_account_id": str(accounts["customer"].id),
        "amount_minor": 10000,
        "currency": "INR",
    }


def test_payment_router(client):
    response = client.get("/api/v1/payments/test")

    assert response.status_code == 200
    assert response.json()["message"] == "Payment router is working!"


def test_create_payment_success(client, accounts, db_session):
    response = client.post(
        "/api/v1/payments",
        json=payment_payload(accounts),
        headers={"Idempotency-Key": "payment-test-001"},
    )

    assert response.status_code == 201, response.json()

    data = response.json()

    assert data["amount_minor"] == 10000
    assert data["currency"] == "INR"
    assert data["status"] == "pending"
    assert data["merchant_account_id"] == str(accounts["merchant"].id)

    payment = db_session.get(Payment, data["id"])

    assert payment is not None
    assert payment.status == "pending"

    event = db_session.execute(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == payment.id
        )
    ).scalar_one()

    assert event.event_type == "payment.created"
    assert event.status == "pending"


def test_create_payment_requires_idempotency_key(client, accounts):
    response = client.post(
        "/api/v1/payments",
        json=payment_payload(accounts),
    )

    assert response.status_code == 422


def test_create_payment_rejects_zero_amount(client, accounts):
    payload = payment_payload(accounts)
    payload["amount_minor"] = 0

    response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": "payment-zero-001"},
    )

    assert response.status_code == 422


def test_create_payment_rejects_negative_amount(client, accounts):
    payload = payment_payload(accounts)
    payload["amount_minor"] = -100

    response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": "payment-negative-001"},
    )

    assert response.status_code == 422


def test_create_payment_rejects_same_accounts(
    client,
    accounts,
):
    payload = payment_payload(accounts)

    # The authenticated merchant cannot also be the customer.
    payload["customer_account_id"] = str(accounts["merchant"].id)

    response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": "payment-same-account-001"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Customer and merchant accounts must be different"
    )


def test_create_payment_rejects_invalid_customer(client, accounts):
    payload = payment_payload(accounts)
    payload["customer_account_id"] = (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": "payment-invalid-customer-001"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Customer account not found"


def test_create_payment_rejects_invalid_merchant_api_key(
    client,
    accounts,
):
    response = client.post(
        "/api/v1/payments",
        json=payment_payload(accounts),
        headers={
            "Idempotency-Key": "payment-invalid-merchant-001",
            "X-Merchant-API-Key": "lc_live_invalid_test_key",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Invalid or expired merchant API key"
    )


def test_create_payment_rejects_currency_mismatch(
    client,
    accounts,
    account_factory,
):
    usd_customer = account_factory(
        account_number="TEST-CUSTOMER-USD",
        name="USD Customer",
        currency="USD",
    )

    payload = payment_payload(accounts)
    payload["customer_account_id"] = str(usd_customer.id)

    response = client.post(
        "/api/v1/payments",
        json=payload,
        headers={"Idempotency-Key": "payment-currency-001"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Customer account currency mismatch"
    )


def test_payment_creation_does_not_post_ledger_entries(
    client,
    accounts,
    db_session,
):
    response = client.post(
        "/api/v1/payments",
        json=payment_payload(accounts),
        headers={"Idempotency-Key": "payment-no-ledger-001"},
    )

    assert response.status_code == 201, response.json()

    payment_count = db_session.scalar(
        select(func.count()).select_from(Payment)
    )

    assert payment_count == 1

    # Payment creation alone must not move money.
    from app.models.ledger_entry import LedgerEntry

    entry_count = db_session.scalar(
        select(func.count()).select_from(LedgerEntry)
    )

    assert entry_count == 0


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
