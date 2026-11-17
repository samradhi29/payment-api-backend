
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.webhook_endpoint import WebhookEndpoint
from app.schemas.webhook import (
    SUPPORTED_WEBHOOK_EVENTS,
    WebhookEndpointCreate,
)
from app.services.webhook_secret_service import (
    encrypt_webhook_secret,
    generate_webhook_secret,
)
from app.services.webhook_url_service import validate_webhook_url


class WebhookServiceError(Exception):
    pass


def create_webhook_endpoint(
    db: Session,
    merchant_account_id: UUID,
    request: WebhookEndpointCreate,
) -> tuple[WebhookEndpoint, str]:
    merchant = db.execute(
        select(Account).where(Account.id == merchant_account_id)
    ).scalar_one_or_none()

    if merchant is None:
        raise WebhookServiceError("Merchant account not found")

    invalid_events = (
        set(request.subscribed_events) - SUPPORTED_WEBHOOK_EVENTS
    )

    if invalid_events:
        raise WebhookServiceError(
            f"Unsupported webhook events: {', '.join(sorted(invalid_events))}"
        )

    normalized_events = sorted(set(request.subscribed_events))

    if not normalized_events:
        raise WebhookServiceError(
            "At least one supported event must be selected"
        )

    safe_url = validate_webhook_url(str(request.url))

    existing = db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.merchant_account_id == merchant_account_id,
            WebhookEndpoint.url == safe_url,
        )
    ).scalar_one_or_none()

    # An active endpoint with this URL cannot be registered again.
    if existing and existing.is_active:
        raise WebhookServiceError(
            "This webhook URL is already registered"
        )

    # Reactivate an inactive endpoint and rotate its secret.
    if existing:
        secret = generate_webhook_secret()

        existing.secret_encrypted = encrypt_webhook_secret(secret)
        existing.subscribed_events = normalized_events
        existing.is_active = True

        try:
            db.commit()
            db.refresh(existing)
        except Exception:
            db.rollback()
            raise

        return existing, secret

    # Create a new endpoint when this URL has never been registered.
    secret = generate_webhook_secret()
    encrypted_secret = encrypt_webhook_secret(secret)

    endpoint = WebhookEndpoint(
        merchant_account_id=merchant_account_id,
        url=safe_url,
        secret_encrypted=encrypted_secret,
        subscribed_events=normalized_events,
        is_active=True,
    )

    try:
        db.add(endpoint)
        db.commit()
        db.refresh(endpoint)
    except IntegrityError as exc:
        db.rollback()
        raise WebhookServiceError(
            "This webhook URL is already registered"
        ) from exc
    except Exception:
        db.rollback()
        raise

    return endpoint, secret


def list_webhook_endpoints(
    db: Session,
    merchant_account_id: UUID,
) -> list[WebhookEndpoint]:
    statement = (
        select(WebhookEndpoint)
        .where(
            WebhookEndpoint.merchant_account_id == merchant_account_id
        )
        .order_by(WebhookEndpoint.created_at.desc())
    )

    return list(db.execute(statement).scalars().all())


def deactivate_webhook_endpoint(
    db: Session,
    merchant_account_id: UUID,
    endpoint_id: UUID,
) -> WebhookEndpoint | None:
    endpoint = db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.merchant_account_id == merchant_account_id,
        )
    ).scalar_one_or_none()

    if endpoint is None:
        return None

    endpoint.is_active = False
    db.commit()
    db.refresh(endpoint)

    return endpoint
