import hashlib

import pytest

from app.providers.payu import PayUAdapter


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("PAYU_MERCHANT_KEY", "test_merchant_key")
    monkeypatch.setenv("PAYU_MERCHANT_SALT", "test_merchant_salt")
    monkeypatch.setenv("PAYU_ENV", "sandbox")
    return PayUAdapter()


def sha512(value):
    return hashlib.sha512(value.encode("utf-8")).hexdigest()


def response_data(adapter):
    return {
        "status": "success",
        "txnid": "payment123456",
        "amount": "100.00",
        "productinfo": "LedgerCore Payment",
        "firstname": "Test",
        "email": "test@example.com",
        "key": adapter.merchant_key,
        "udf1": "payment-id-123",
        "udf2": "",
        "udf3": "",
        "udf4": "",
        "udf5": "",
    }


def expected_hash(adapter, data, split_info=None, additional_charges=None):
    parts = [adapter.merchant_salt, data["status"]]

    if split_info is not None:
        parts.append(split_info)

    parts.extend([""] * 5)
    parts.extend([
        data["udf5"],
        data["udf4"],
        data["udf3"],
        data["udf2"],
        data["udf1"],
        data["email"],
        data["firstname"],
        data["productinfo"],
        data["amount"],
        data["txnid"],
        data["key"],
    ])

    value = "|".join(parts)

    if additional_charges is not None:
        value = f"{additional_charges}|{value}"

    return sha512(value)


def test_request_hash_matches_payu_formula(adapter):
    actual = adapter._request_hash(
        txnid="payment123456",
        amount="100.00",
        productinfo="LedgerCore Payment",
        firstname="Test",
        email="test@example.com",
        udf1="payment-id-123",
    )

    expected = "|".join([
        adapter.merchant_key,
        "payment123456",
        "100.00",
        "LedgerCore Payment",
        "Test",
        "test@example.com",
        "payment-id-123",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        adapter.merchant_salt,
    ])

    assert actual == sha512(expected)


def test_valid_normal_response_hash(adapter):
    data = response_data(adapter)
    data["hash"] = expected_hash(adapter, data)
    assert adapter.verify_response_hash(data) is True


def test_valid_split_info_response_hash(adapter):
    data = response_data(adapter)
    split_info = '{"type":"absolute","value":10}'
    data["splitInfo"] = split_info
    data["hash"] = expected_hash(adapter, data, split_info=split_info)
    assert adapter.verify_response_hash(data) is True


def test_valid_response_hash_with_additional_charges(adapter):
    data = response_data(adapter)
    charges = "5.00"
    data["additional_charges"] = charges
    data["hash"] = expected_hash(adapter, data, additional_charges=charges)
    assert adapter.verify_response_hash(data) is True


def test_invalid_response_hash_is_rejected(adapter):
    data = response_data(adapter)
    data["hash"] = expected_hash(adapter, data)
    data["amount"] = "200.00"
    assert adapter.verify_response_hash(data) is False


def test_wrong_merchant_key_is_rejected(adapter):
    data = response_data(adapter)
    data["key"] = "wrong_merchant_key"
    data["hash"] = expected_hash(adapter, data)
    assert adapter.verify_response_hash(data) is False


def test_missing_required_field_is_rejected(adapter):
    data = response_data(adapter)
    data["hash"] = expected_hash(adapter, data)
    del data["email"]
    assert adapter.verify_response_hash(data) is False
