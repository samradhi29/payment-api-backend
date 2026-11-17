import pytest
from sqlalchemy import text

from app.models.payment import Payment
from app.providers.base import GatewayPayment


@pytest.fixture
def payu_payment(db_session, accounts, account_factory):
    account_factory(
        account_number="FUND-1",
        name="Payment Clearing Account",
        account_type="asset",
        normal_side="debit",
    )

    payment = Payment(
        customer_account_id=accounts["customer"].id,
        merchant_account_id=accounts["merchant"].id,
        amount_minor=10000,
        currency="INR",
        status="pending",
        provider="payu",
        provider_order_id="payu_callback_test_001",
        customer_phone="9876543210",
        customer_email="customer@example.com",
        request_hash="a" * 64,
    )

    db_session.add(payment)
    db_session.commit()
    db_session.refresh(payment)

    return payment


def callback_payload(payment, status="success", amount="100.00"):
    return {
        "key": "test-merchant-key",
        "txnid": payment.provider_order_id,
        "amount": amount,
        "productinfo": "LedgerCore Payment",
        "firstname": "Test",
        "email": "customer@example.com",
        "phone": "9876543210",
        "status": status,
        "hash": "test-callback-hash",
    }


def ledger_counts(db_session):
    transactions = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_transactions")
    ).scalar_one()

    entries = db_session.execute(
        text("SELECT COUNT(*) FROM ledger_entries")
    ).scalar_one()

    return transactions, entries


def mock_payu_verification(monkeypatch, gateway_payment):
    calls = []

    class FakePayUAdapter:
        def fetch_payment(self, provider_order_id):
            calls.append(provider_order_id)
            return gateway_payment

    monkeypatch.setattr(
        "app.services.payment_verification_service.get_gateway_adapter",
        lambda provider: FakePayUAdapter(),
    )

    return calls


def successful_gateway_payment(payment):
    return GatewayPayment(
        provider="payu",
        provider_payment_id="payu_verified_payment_001",
        provider_order_id=payment.provider_order_id,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        status="success",
        captured=True,
    )


def test_payu_success_callback_verifies_and_posts_ledger(
    client,
    db_session,
    payu_payment,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.payments._verify_payu_callback_hash",
        lambda data: True,
    )

    calls = mock_payu_verification(
        monkeypatch,
        successful_gateway_payment(payu_payment),
    )

    response = client.post(
        "/api/v1/payments/payu/success",
        data=callback_payload(payu_payment),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["already_processed"] is False

    db_session.refresh(payu_payment)

    assert payu_payment.status == "succeeded"
    assert (
        payu_payment.provider_payment_id
        == "payu_verified_payment_001"
    )

    assert calls == [payu_payment.provider_order_id]
    assert ledger_counts(db_session) == (1, 2)


def test_payu_callback_rejects_invalid_hash_without_ledger_posting(
    client,
    db_session,
    payu_payment,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.payments._verify_payu_callback_hash",
        lambda data: False,
    )

    calls = mock_payu_verification(
        monkeypatch,
        successful_gateway_payment(payu_payment),
    )

    response = client.post(
        "/api/v1/payments/payu/success",
        data=callback_payload(payu_payment),
    )

    assert response.status_code == 400
    assert "Invalid PayU callback hash" in response.json()["detail"]

    db_session.refresh(payu_payment)

    assert payu_payment.status == "pending"
    assert payu_payment.provider_payment_id is None
    assert calls == []
    assert ledger_counts(db_session) == (0, 0)


def test_payu_callback_rejects_amount_mismatch(
    client,
    db_session,
    payu_payment,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.payments._verify_payu_callback_hash",
        lambda data: True,
    )

    calls = mock_payu_verification(
        monkeypatch,
        successful_gateway_payment(payu_payment),
    )

    response = client.post(
        "/api/v1/payments/payu/success",
        data=callback_payload(
            payu_payment,
            amount="999.00",
        ),
    )

    assert response.status_code == 400
    assert "amount does not match" in response.json()["detail"].lower()

    db_session.refresh(payu_payment)

    assert payu_payment.status == "pending"
    assert payu_payment.provider_payment_id is None
    assert calls == []
    assert ledger_counts(db_session) == (0, 0)


def test_payu_callback_does_not_post_when_server_verification_fails(
    client,
    db_session,
    payu_payment,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.payments._verify_payu_callback_hash",
        lambda data: True,
    )

    failed_gateway_payment = GatewayPayment(
        provider="payu",
        provider_payment_id="payu_failed_payment_001",
        provider_order_id=payu_payment.provider_order_id,
        amount_minor=payu_payment.amount_minor,
        currency=payu_payment.currency,
        status="failure",
        captured=False,
    )

    calls = mock_payu_verification(
        monkeypatch,
        failed_gateway_payment,
    )

    response = client.post(
        "/api/v1/payments/payu/success",
        data=callback_payload(payu_payment),
    )

    assert response.status_code == 400

    db_session.refresh(payu_payment)

    assert payu_payment.status == "pending"
    assert payu_payment.provider_payment_id is None
    assert calls == [payu_payment.provider_order_id]
    assert ledger_counts(db_session) == (0, 0)


def test_payu_failure_callback_does_not_post_ledger(
    client,
    db_session,
    payu_payment,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.payments._verify_payu_callback_hash",
        lambda data: True,
    )

    calls = mock_payu_verification(
        monkeypatch,
        successful_gateway_payment(payu_payment),
    )

    response = client.post(
        "/api/v1/payments/payu/failure",
        data=callback_payload(
            payu_payment,
            status="failure",
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "not_successful"

    db_session.refresh(payu_payment)

    assert payu_payment.status == "pending"
    assert payu_payment.provider_payment_id is None
    assert calls == []
    assert ledger_counts(db_session) == (0, 0)


def test_duplicate_payu_success_callback_does_not_duplicate_ledger(
    client,
    db_session,
    payu_payment,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.api.payments._verify_payu_callback_hash",
        lambda data: True,
    )

    calls = mock_payu_verification(
        monkeypatch,
        successful_gateway_payment(payu_payment),
    )

    first_response = client.post(
        "/api/v1/payments/payu/success",
        data=callback_payload(payu_payment),
    )

    second_response = client.post(
        "/api/v1/payments/payu/success",
        data=callback_payload(payu_payment),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    assert first_response.json()["already_processed"] is False
    assert second_response.json()["already_processed"] is True

    db_session.refresh(payu_payment)

    assert payu_payment.status == "succeeded"
    assert (
        payu_payment.provider_payment_id
        == "payu_verified_payment_001"
    )

    # The second callback uses the existing verified ledger transaction.
    assert calls == [payu_payment.provider_order_id]
    assert ledger_counts(db_session) == (1, 2)
