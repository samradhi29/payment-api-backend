
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outbox_event import OutboxEvent
from app.models.webhook_delivery import WebhookDelivery
from app.models.webhook_endpoint import WebhookEndpoint
from app.services.webhook_secret_service import decrypt_webhook_secret
from app.services.webhook_signing_service import (
    build_webhook_headers,
    serialize_webhook_payload,
)
from app.services.webhook_url_service import (
    PinnedPublicHTTPTransport,
    WebhookURLValidationError,
    validate_webhook_url,
)


logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 8
REQUEST_TIMEOUT_SECONDS = 10.0
PROCESSING_LEASE_SECONDS = 60

MAX_RESPONSE_BODY_LENGTH = 4000
BASE_RETRY_DELAY_SECONDS = 5
MAX_RETRY_DELAY_SECONDS = 3600


def _build_event_payload(event: OutboxEvent) -> dict:
    return {
        "id": str(event.id),
        "type": event.event_type,
        "created_at": (
            event.created_at.isoformat()
            if event.created_at
            else datetime.now(timezone.utc).isoformat()
        ),
        "data": event.payload or {},
    }


def _retry_delay(attempt: int) -> int:
    delay = BASE_RETRY_DELAY_SECONDS * (2 ** attempt)
    return min(delay, MAX_RETRY_DELAY_SECONDS)


def _finish_delivery(
    db: Session,
    delivery_id: UUID,
    claim_token: UUID,
    *,
    status: str,
    response_status: int | None = None,
    response_body: str | None = None,
    error: str | None = None,
    retry_delay: int | None = None,
) -> bool:
    delivery = db.execute(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.status == "processing",
            WebhookDelivery.claim_token == claim_token,
        )
        .with_for_update()
    ).scalar_one_or_none()

    # Another worker may already have recovered and reclaimed it.
    if delivery is None:
        db.rollback()
        logger.warning(
            "Ignoring stale webhook worker result | Delivery ID: %s",
            delivery_id,
        )
        return False

    now = datetime.now(timezone.utc)

    delivery.status = status
    delivery.last_response_status = response_status
    delivery.last_response_body = (
        response_body[:MAX_RESPONSE_BODY_LENGTH]
        if response_body
        else None
    )
    delivery.last_error = error

    delivery.processing_started_at = None
    delivery.lease_expires_at = None
    delivery.claim_token = None

    if status == "delivered":
        delivery.delivered_at = now
        delivery.last_error = None

    elif status == "pending" and retry_delay is not None:
        delivery.available_at = now + timedelta(seconds=retry_delay)

    delivery.updated_at = now

    db.commit()
    return True


def dispatch_webhook_delivery(
    db: Session,
    delivery_id: UUID,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """
    Attempt to deliver one webhook.

    A unique claim token prevents an expired worker from updating
    a delivery that has already been reclaimed by another worker.

    Returns True only when the delivery succeeds.
    """

    now = datetime.now(timezone.utc)
    claim_token = uuid4()

    statement = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.status == "pending",
            WebhookDelivery.available_at <= now,
        )
        .with_for_update(skip_locked=True)
    )

    try:
        delivery = db.execute(statement).scalar_one_or_none()

        if delivery is None:
            db.rollback()
            return False

        delivery.status = "processing"
        delivery.attempts += 1
        delivery.processing_started_at = now
        delivery.lease_expires_at = (
            now + timedelta(seconds=PROCESSING_LEASE_SECONDS)
        )
        delivery.claim_token = claim_token
        delivery.updated_at = now

        endpoint_id = delivery.webhook_endpoint_id
        event_id = delivery.outbox_event_id
        attempt = delivery.attempts

        db.commit()

    except Exception:
        db.rollback()
        raise

    try:
        endpoint = db.get(WebhookEndpoint, endpoint_id)
        event = db.get(OutboxEvent, event_id)

        if endpoint is None or event is None:
            _finish_delivery(
                db,
                delivery_id,
                claim_token,
                status="dead_letter",
                error="Webhook endpoint or outbox event no longer exists",
            )
            return False

        if not endpoint.is_active:
            _finish_delivery(
                db,
                delivery_id,
                claim_token,
                status="dead_letter",
                error="Webhook endpoint is inactive",
            )
            return False

        safe_url = validate_webhook_url(endpoint.url)
        secret = decrypt_webhook_secret(endpoint.secret_encrypted)

        payload = serialize_webhook_payload(
            _build_event_payload(event)
        )

        headers = build_webhook_headers(
            secret=secret,
            payload=payload,
        )

        headers["X-LedgerCore-Event-ID"] = str(event.id)
        headers["X-LedgerCore-Event-Type"] = event.event_type

        # In production, pin the connection to a vetted public IP. This avoids
        # a second DNS lookup at connect time (DNS-rebinding/TOCTOU protection).
        # A caller-supplied transport is retained for deterministic unit tests.
        active_transport = (
            transport if transport is not None
            else PinnedPublicHTTPTransport(safe_url)
        )

        with httpx.Client(
            transport=active_transport,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = client.post(
                safe_url,
                content=payload,
                headers=headers,
            )

        response_body = response.text[:MAX_RESPONSE_BODY_LENGTH]

        if 200 <= response.status_code < 300:
            return _finish_delivery(
                db,
                delivery_id,
                claim_token,
                status="delivered",
                response_status=response.status_code,
                response_body=response_body,
            )

        retryable = (
            response.status_code == 408
            or response.status_code == 429
            or response.status_code >= 500
        )

        if retryable and attempt < MAX_DELIVERY_ATTEMPTS:
            _finish_delivery(
                db,
                delivery_id,
                claim_token,
                status="pending",
                response_status=response.status_code,
                response_body=response_body,
                error=f"Retryable HTTP response: {response.status_code}",
                retry_delay=_retry_delay(attempt),
            )
        else:
            _finish_delivery(
                db,
                delivery_id,
                claim_token,
                status="dead_letter",
                response_status=response.status_code,
                response_body=response_body,
                error=f"Non-retryable HTTP response: {response.status_code}",
            )

        return False

    except WebhookURLValidationError as exc:
        _finish_delivery(
            db,
            delivery_id,
            claim_token,
            status="dead_letter",
            error=f"Unsafe webhook destination: {exc}",
        )
        return False

    except (ValueError, RuntimeError) as exc:
        logger.exception(
            "Webhook configuration error: %s",
            delivery_id,
        )

        _finish_delivery(
            db,
            delivery_id,
            claim_token,
            status="dead_letter",
            error=str(exc)[:MAX_RESPONSE_BODY_LENGTH],
        )
        return False

    except httpx.RequestError as exc:
        if attempt < MAX_DELIVERY_ATTEMPTS:
            _finish_delivery(
                db,
                delivery_id,
                claim_token,
                status="pending",
                error=f"HTTP request failed: {str(exc)}",
                retry_delay=_retry_delay(attempt),
            )
        else:
            _finish_delivery(
                db,
                delivery_id,
                claim_token,
                status="dead_letter",
                error=(
                    "HTTP request failed after maximum attempts: "
                    f"{str(exc)}"
                ),
            )

        return False

    except Exception:
        db.rollback()

        logger.exception(
            "Unexpected webhook delivery error: %s",
            delivery_id,
        )

        # Keep the processing lease. The worker recovery mechanism
        # will make this delivery available after lease expiration.
        raise
