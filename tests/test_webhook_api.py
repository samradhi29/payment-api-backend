
from uuid import UUID

import pytest

from app.models.webhook_endpoint import WebhookEndpoint
from app.services.merchant_api_key_service import create_merchant_api_key


@pytest.fixture
def allow_webhook_urls(monkeypatch):
    monkeypatch.setattr(
        "app.services.webhook_service.validate_webhook_url",
        lambda url: url,
    )


def create_webhook_payload(
    url="https://example.com/webhooks",
    events=None,
):
    return {
        "url": url,
        "subscribed_events": events or ["payment.created", "payment.succeeded"],
    }


def test_register_webhook_endpoint(
    client,
    db_session,
    accounts,
    allow_webhook_urls,
):
    response = client.post(
        "/api/v1/webhooks",
        json=create_webhook_payload(),
    )

    assert response.status_code == 201

    data = response.json()

    assert data["merchant_account_id"] == str(accounts["merchant"].id)
    assert data["is_active"] is True
    assert data["subscribed_events"] == [
        "payment.created",
        "payment.succeeded",
    ]
    assert data["secret"]
    assert len(data["secret"]) > 20

    endpoint = db_session.get(
        WebhookEndpoint,
        UUID(data["id"]),
    )

    assert endpoint is not None
    assert endpoint.secret_encrypted != data["secret"]


def test_register_webhook_rejects_unsupported_event(
    client,
    allow_webhook_urls,
):
    response = client.post(
        "/api/v1/webhooks",
        json=create_webhook_payload(
            events=["payment.refunded"],
        ),
    )

    assert response.status_code == 400
    assert "Unsupported webhook events" in response.json()["detail"]


def test_register_webhook_rejects_duplicate_url(
    client,
    allow_webhook_urls,
):
    payload = create_webhook_payload()

    first_response = client.post(
        "/api/v1/webhooks",
        json=payload,
    )

    second_response = client.post(
        "/api/v1/webhooks",
        json=payload,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 400
    assert second_response.json()["detail"] == (
        "This webhook URL is already registered"
    )


def test_list_webhooks_only_returns_authenticated_merchants_endpoints(
    client,
    accounts,
    account_factory,
    db_session,
    allow_webhook_urls,
):
    first_merchant_key = client.headers["X-Merchant-API-Key"]

    first_response = client.post(
        "/api/v1/webhooks",
        json=create_webhook_payload(
            url="https://first.example.com/webhooks",
        ),
    )

    assert first_response.status_code == 201

    second_merchant = account_factory(
        account_number="TEST-MERCHANT-002",
        name="Second Test Merchant",
    )

    _, second_merchant_key = create_merchant_api_key(
        db=db_session,
        account_id=second_merchant.id,
        label="Second Merchant Webhook Test Key",
    )

    client.headers["X-Merchant-API-Key"] = second_merchant_key

    second_response = client.post(
        "/api/v1/webhooks",
        json=create_webhook_payload(
            url="https://second.example.com/webhooks",
        ),
    )

    assert second_response.status_code == 201

    second_list_response = client.get("/api/v1/webhooks")

    assert second_list_response.status_code == 200

    second_list = second_list_response.json()

    assert len(second_list) == 1
    assert second_list[0]["merchant_account_id"] == str(second_merchant.id)
    assert "secret" not in second_list[0]

    client.headers["X-Merchant-API-Key"] = first_merchant_key

    first_list_response = client.get("/api/v1/webhooks")

    assert first_list_response.status_code == 200

    first_list = first_list_response.json()

    assert len(first_list) == 1
    assert first_list[0]["merchant_account_id"] == str(accounts["merchant"].id)
    assert "secret" not in first_list[0]


def test_merchant_cannot_deactivate_another_merchants_webhook(
    client,
    account_factory,
    db_session,
    allow_webhook_urls,
):
    first_merchant_key = client.headers["X-Merchant-API-Key"]

    create_response = client.post(
        "/api/v1/webhooks",
        json=create_webhook_payload(),
    )

    assert create_response.status_code == 201

    endpoint_id = create_response.json()["id"]

    second_merchant = account_factory(
        account_number="TEST-MERCHANT-003",
        name="Another Test Merchant",
    )

    _, second_merchant_key = create_merchant_api_key(
        db=db_session,
        account_id=second_merchant.id,
        label="Another Merchant Test Key",
    )

    client.headers["X-Merchant-API-Key"] = second_merchant_key

    unauthorized_delete = client.delete(
        f"/api/v1/webhooks/{endpoint_id}",
    )

    assert unauthorized_delete.status_code == 404

    client.headers["X-Merchant-API-Key"] = first_merchant_key

    authorized_delete = client.delete(
        f"/api/v1/webhooks/{endpoint_id}",
    )

    assert authorized_delete.status_code == 200
    assert authorized_delete.json()["is_active"] is False

    endpoint = db_session.get(
        WebhookEndpoint,
        UUID(endpoint_id),
    )

    assert endpoint is not None
    assert endpoint.is_active is False


def test_deleting_unknown_webhook_returns_404(client):
    response = client.delete(
        "/api/v1/webhooks/00000000-0000-0000-0000-000000000000",
    )

    assert response.status_code == 404
