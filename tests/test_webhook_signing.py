
import pytest

from app.services.webhook_signing_service import (
    build_webhook_headers,
    generate_webhook_signature,
    serialize_webhook_payload,
    verify_webhook_signature,
)


SECRET = "test_webhook_signing_secret_123456"


def test_generate_webhook_signature():
    payload = b'{"amount_minor":10000,"status":"succeeded"}'

    signature, timestamp = generate_webhook_signature(
        SECRET,
        payload,
        timestamp=1700000000,
    )

    assert len(signature) == 64
    assert timestamp == 1700000000


def test_same_payload_and_timestamp_produce_same_signature():
    payload = b'{"payment_id":"payment-123"}'

    first_signature, _ = generate_webhook_signature(
        SECRET, payload, timestamp=1700000000
    )
    second_signature, _ = generate_webhook_signature(
        SECRET, payload, timestamp=1700000000
    )

    assert first_signature == second_signature


def test_different_secrets_produce_different_signatures():
    payload = b'{"payment_id":"payment-123"}'

    first_signature, _ = generate_webhook_signature(
        SECRET, payload, timestamp=1700000000
    )
    second_signature, _ = generate_webhook_signature(
        "another_secret", payload, timestamp=1700000000
    )

    assert first_signature != second_signature


def test_verify_valid_signature():
    payload = b'{"payment_id":"payment-123"}'
    timestamp = 1700000000

    signature, _ = generate_webhook_signature(
        SECRET, payload, timestamp=timestamp
    )

    assert verify_webhook_signature(
        SECRET,
        payload,
        f"sha256={signature}",
        timestamp,
        current_time=timestamp,
    )


def test_reject_modified_payload():
    original_payload = b'{"amount_minor":10000}'
    modified_payload = b'{"amount_minor":20000}'
    timestamp = 1700000000

    signature, _ = generate_webhook_signature(
        SECRET, original_payload, timestamp=timestamp
    )

    assert not verify_webhook_signature(
        SECRET,
        modified_payload,
        f"sha256={signature}",
        timestamp,
        current_time=timestamp,
    )


def test_reject_wrong_secret():
    payload = b'{"payment_id":"payment-123"}'
    timestamp = 1700000000

    signature, _ = generate_webhook_signature(
        SECRET, payload, timestamp=timestamp
    )

    assert not verify_webhook_signature(
        "incorrect_secret",
        payload,
        f"sha256={signature}",
        timestamp,
        current_time=timestamp,
    )


def test_reject_expired_timestamp():
    payload = b'{"payment_id":"payment-123"}'
    timestamp = 1700000000

    signature, _ = generate_webhook_signature(
        SECRET, payload, timestamp=timestamp
    )

    assert not verify_webhook_signature(
        SECRET,
        payload,
        f"sha256={signature}",
        timestamp,
        current_time=timestamp + 600,
        tolerance_seconds=300,
    )


def test_reject_future_timestamp_outside_tolerance():
    payload = b'{"payment_id":"payment-123"}'
    timestamp = 1700000000

    signature, _ = generate_webhook_signature(
        SECRET, payload, timestamp=timestamp
    )

    assert not verify_webhook_signature(
        SECRET,
        payload,
        f"sha256={signature}",
        timestamp,
        current_time=timestamp - 600,
        tolerance_seconds=300,
    )


def test_reject_missing_signature():
    assert not verify_webhook_signature(
        SECRET,
        b'{"test":true}',
        "",
        1700000000,
        current_time=1700000000,
    )


def test_reject_negative_tolerance():
    signature, _ = generate_webhook_signature(
        SECRET,
        b'{"test":true}',
        timestamp=1700000000,
    )

    assert not verify_webhook_signature(
        SECRET,
        b'{"test":true}',
        f"sha256={signature}",
        1700000000,
        tolerance_seconds=-1,
        current_time=1700000000,
    )


def test_build_webhook_headers():
    payload = b'{"payment_id":"payment-123"}'

    headers = build_webhook_headers(
        SECRET, payload, timestamp=1700000000
    )

    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == "LedgerCore-Webhooks/1.0"
    assert headers["X-LedgerCore-Timestamp"] == "1700000000"
    assert headers["X-LedgerCore-Signature"].startswith("sha256=")


def test_payload_serialization_is_deterministic():
    first = serialize_webhook_payload(
        {"payment_id": "123", "amount_minor": 10000}
    )
    second = serialize_webhook_payload(
        {"amount_minor": 10000, "payment_id": "123"}
    )

    assert first == second


def test_empty_secret_is_rejected():
    with pytest.raises(ValueError):
        generate_webhook_signature(
            "", b'{"test":true}', timestamp=1700000000
        )


def test_non_bytes_payload_is_rejected():
    with pytest.raises(TypeError):
        generate_webhook_signature(
            SECRET, '{"test":true}', timestamp=1700000000
        )
