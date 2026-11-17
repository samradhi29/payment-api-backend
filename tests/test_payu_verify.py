import hashlib

import pytest

from app.providers.base import GatewayError
from app.providers.payu import PayUAdapter


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("PAYU_MERCHANT_KEY", "test_merchant_key")
    monkeypatch.setenv("PAYU_MERCHANT_SALT", "test_merchant_salt")
    monkeypatch.setenv("PAYU_ENV", "sandbox")
    return PayUAdapter()


def make_api_response(transaction):
    return {
        "status": 1,
        "msg": "Transaction details retrieved",
        "transaction_details": {
            "payment123456": transaction
        },
    }


def mock_response(payload):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return FakeResponse()


def test_verify_payment_success(adapter, monkeypatch):
    payload = make_api_response({
        "mihpayid": "987654321",
        "txnid": "payment123456",
        "amt": "100.00",
        "status": "success",
    })

    captured = {}

    def fake_post(url, data, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        return mock_response(payload)

    monkeypatch.setattr("app.providers.payu.httpx.post", fake_post)

    result = adapter.fetch_payment("payment123456")

    assert result.provider == "payu"
    assert result.provider_payment_id == "987654321"
    assert result.provider_order_id == "payment123456"
    assert result.amount_minor == 10000
    assert result.currency == "INR"
    assert result.status == "success"
    assert result.captured is True

    assert captured["data"]["command"] == "verify_payment"
    assert captured["data"]["var1"] == "payment123456"
    assert captured["timeout"] == 20.0

    expected_hash_string = "|".join([
        adapter.merchant_key,
        "verify_payment",
        "payment123456",
        adapter.merchant_salt,
    ])

    expected_hash = hashlib.sha512(
        expected_hash_string.encode("utf-8")
    ).hexdigest()

    assert captured["data"]["hash"] == expected_hash


def test_verify_payment_failed_transaction(adapter, monkeypatch):
    payload = make_api_response({
        "mihpayid": "987654321",
        "txnid": "payment123456",
        "amt": "100.00",
        "status": "failure",
    })

    monkeypatch.setattr(
        "app.providers.payu.httpx.post",
        lambda *args, **kwargs: mock_response(payload),
    )

    result = adapter.fetch_payment("payment123456")

    assert result.status == "failure"
    assert result.captured is False
    assert result.amount_minor == 10000


def test_verify_payment_rejects_api_failure(adapter, monkeypatch):
    payload = {
        "status": 0,
        "msg": "Invalid transaction",
    }

    monkeypatch.setattr(
        "app.providers.payu.httpx.post",
        lambda *args, **kwargs: mock_response(payload),
    )

    with pytest.raises(GatewayError, match="PayU verification failed"):
        adapter.fetch_payment("payment123456")


def test_verify_payment_rejects_missing_transaction(adapter, monkeypatch):
    payload = {
        "status": 1,
        "msg": "Transaction details retrieved",
        "transaction_details": {},
    }

    monkeypatch.setattr(
        "app.providers.payu.httpx.post",
        lambda *args, **kwargs: mock_response(payload),
    )

    with pytest.raises(GatewayError, match="transaction details"):
        adapter.fetch_payment("payment123456")


def test_verify_payment_rejects_invalid_amount(adapter, monkeypatch):
    payload = make_api_response({
        "mihpayid": "987654321",
        "txnid": "payment123456",
        "amt": "invalid",
        "status": "success",
    })

    monkeypatch.setattr(
        "app.providers.payu.httpx.post",
        lambda *args, **kwargs: mock_response(payload),
    )

    with pytest.raises(GatewayError, match="transaction amount"):
        adapter.fetch_payment("payment123456")
