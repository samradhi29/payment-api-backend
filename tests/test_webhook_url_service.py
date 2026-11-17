
import pytest

from app.services.webhook_url_service import (
    WebhookURLValidationError,
    validate_webhook_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/webhook",
        "http://localhost/webhook",
        "https://localhost/webhook",
        "https://127.0.0.1/webhook",
        "https://10.0.0.1/webhook",
        "https://192.168.1.10/webhook",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/webhook",
        "https://[fc00::1]/webhook",
        "https://internal.local/webhook",
        "https://example.com:8443/webhook",
        "https://user:password@example.com/webhook",
        "https://example.com/webhook#fragment",
        "not-a-url",
        "",
    ],
)
def test_rejects_unsafe_or_invalid_urls(url):
    with pytest.raises(WebhookURLValidationError):
        validate_webhook_url(url)


def test_rejects_hostname_that_resolves_to_private_ip(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [
            (
                2,
                1,
                6,
                "",
                ("192.168.1.10", 443),
            )
        ]

    monkeypatch.setattr(
        "app.services.webhook_url_service.socket.getaddrinfo",
        fake_getaddrinfo,
    )

    with pytest.raises(WebhookURLValidationError):
        validate_webhook_url("https://example.com/webhook")


def test_rejects_hostname_with_mixed_public_and_private_ips(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.5", 443)),
        ]

    monkeypatch.setattr(
        "app.services.webhook_url_service.socket.getaddrinfo",
        fake_getaddrinfo,
    )

    with pytest.raises(WebhookURLValidationError):
        validate_webhook_url("https://example.com/webhook")


def test_accepts_public_https_hostname(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [
            (
                2,
                1,
                6,
                "",
                ("93.184.216.34", 443),
            )
        ]

    monkeypatch.setattr(
        "app.services.webhook_url_service.socket.getaddrinfo",
        fake_getaddrinfo,
    )

    result = validate_webhook_url("https://example.com/webhook")

    assert result == "https://example.com/webhook"


def test_accepts_public_https_ip():
    result = validate_webhook_url("https://8.8.8.8/webhook")

    assert result == "https://8.8.8.8/webhook"
