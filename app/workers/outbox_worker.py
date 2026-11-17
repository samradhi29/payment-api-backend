
import logging
import math
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outbox_event import OutboxEvent
from app.models.webhook_delivery import WebhookDelivery
from app.services.webhook_delivery_service import enqueue_webhook_deliveries
from app.services.webhook_dispatch_service import (
    MAX_DELIVERY_ATTEMPTS,
    dispatch_webhook_delivery,
)


logger = logging.getLogger(__name__)

# Outbox retry configuration
MAX_ATTEMPTS = 5
BASE_RETRY_SECONDS = 5
MAX_RETRY_SECONDS = 300

# Webhook dispatch configuration
WEBHOOK_BATCH_SIZE = 10

SUPPORTED_PAYMENT_EVENTS = {
    "payment.created",
    "payment.succeeded",
    "payment.failed",
}

SUPPORTED_GATEWAY_EVENTS = {
    "payment.gateway.attempt",
}

SUPPORTED_OUTBOX_EVENTS = (
    SUPPORTED_PAYMENT_EVENTS | SUPPORTED_GATEWAY_EVENTS
)

VALID_GATEWAY_OUTCOMES = {
    "success",
    "failure",
    "timeout",
}


def fetch_pending_events(db: Session, batch_size: int = 10):
    """
    Fetch available pending events and lock them.

    Concurrent workers skip events already locked by another worker.
    """

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    statement = (
        select(OutboxEvent)
        .where(
            OutboxEvent.status == "pending",
            OutboxEvent.available_at <= datetime.now(timezone.utc),
        )
        .order_by(
            OutboxEvent.available_at,
            OutboxEvent.created_at,
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    return db.execute(statement).scalars().all()


def handle_outbox_event(event: OutboxEvent):
    """
    Validate supported payment and gateway telemetry events.
    """

    if event.event_type not in SUPPORTED_OUTBOX_EVENTS:
        raise ValueError(
            f"Unsupported event type: {event.event_type}"
        )

    payload = event.payload or {}

    if not isinstance(payload, dict):
        raise ValueError("Outbox event payload must be an object")

    if event.event_type in SUPPORTED_GATEWAY_EVENTS:
        required_fields = [
            "payment_id",
            "gateway",
            "outcome",
            "latency_ms",
        ]

        for field in required_fields:
            if field not in payload:
                raise ValueError(
                    f"Missing field in gateway telemetry event: {field}"
                )

        gateway = payload["gateway"]

        if not isinstance(gateway, str) or not gateway.strip():
            raise ValueError("Gateway name must be a non-empty string")

        if payload["outcome"] not in VALID_GATEWAY_OUTCOMES:
            raise ValueError(
                f"Invalid gateway outcome: {payload['outcome']}"
            )

        latency_ms = payload["latency_ms"]

        if (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or not math.isfinite(latency_ms)
            or latency_ms < 0
        ):
            raise ValueError("Gateway latency must be a finite non-negative number")

        logger.info(
            "Validated gateway telemetry | Payment ID: %s | Gateway: %s "
            "| Outcome: %s | Latency: %.2f ms",
            payload["payment_id"],
            gateway,
            payload["outcome"],
            latency_ms,
        )

        return

    required_fields = [
        "payment_id",
        "merchant_account_id",
    ]

    if event.event_type == "payment.created":
        required_fields.extend([
            "customer_account_id",
            "amount_minor",
            "currency",
            "status",
        ])

    for field in required_fields:
        if field not in payload:
            raise ValueError(
                f"Missing field in payment event: {field}"
            )

    logger.info(
        "Processing %s event | Payment ID: %s",
        event.event_type,
        payload["payment_id"],
    )


def publish_gateway_telemetry(
    event: OutboxEvent,
    kafka_publisher,
):
    """
    Publish a gateway telemetry event to Kafka.

    The database outbox UUID is used as the stable event ID.
    Redis-side deduplication will protect against duplicate delivery
    if Kafka publication succeeds but the database transaction rolls back.
    """

    if kafka_publisher is None:
        raise RuntimeError(
            "Kafka publisher is required for gateway telemetry events"
        )

    payload = dict(event.payload)

    kafka_event = {
        **payload,
        "event_id": str(event.id),
        "event_type": event.event_type,
        "payment_method": payload.get("payment_method") or "UNKNOWN",
        "bank_code": payload.get("bank_code") or "UNKNOWN",
    }

    kafka_publisher.publish(kafka_event)

    logger.info(
        "Published gateway telemetry to Kafka | Event ID: %s | Gateway: %s",
        event.id,
        payload["gateway"],
    )


def mark_event_published(db: Session, event: OutboxEvent):
    """
    Mark an event as successfully processed.
    The caller controls the transaction commit.
    """

    event.status = "published"
    event.published_at = datetime.now(timezone.utc)


def schedule_retry(event: OutboxEvent):
    """
    Schedule retry using exponential backoff.

    After MAX_ATTEMPTS, permanently mark the event as failed.
    """

    event.attempts += 1

    if event.attempts >= MAX_ATTEMPTS:
        event.status = "failed"

        logger.error(
            "Event permanently failed | Event ID: %s | Attempts: %s",
            event.id,
            event.attempts,
        )

        return False

    delay = min(
        BASE_RETRY_SECONDS * (2 ** (event.attempts - 1)),
        MAX_RETRY_SECONDS,
    )

    event.status = "pending"
    event.available_at = (
        datetime.now(timezone.utc) + timedelta(seconds=delay)
    )

    logger.warning(
        "Event retry scheduled | Event ID: %s | Attempt: %s | "
        "Retry in %s seconds",
        event.id,
        event.attempts,
        delay,
    )

    return True


def recover_expired_webhook_deliveries(
    db: Session,
    batch_size: int = WEBHOOK_BATCH_SIZE,
) -> dict:
    """
    Recover webhook deliveries abandoned by crashed workers.

    Only processing deliveries with expired leases are recovered.
    Row locks and SKIP LOCKED allow multiple workers to run recovery.
    """

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    now = datetime.now(timezone.utc)

    statement = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status == "processing",
            WebhookDelivery.lease_expires_at.is_not(None),
            WebhookDelivery.lease_expires_at <= now,
        )
        .order_by(WebhookDelivery.lease_expires_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    try:
        deliveries = db.execute(statement).scalars().all()

        recovered_count = 0
        dead_letter_count = 0

        for delivery in deliveries:
            delivery.processing_started_at = None
            delivery.lease_expires_at = None
            delivery.claim_token = None
            delivery.updated_at = now

            if delivery.attempts >= MAX_DELIVERY_ATTEMPTS:
                delivery.status = "dead_letter"
                delivery.last_error = (
                    "Processing lease expired after maximum delivery attempts"
                )
                dead_letter_count += 1
            else:
                delivery.status = "pending"
                delivery.available_at = now
                delivery.last_error = (
                    "Processing lease expired; delivery recovered"
                )
                recovered_count += 1

            logger.warning(
                "Recovered expired webhook delivery | "
                "Delivery ID: %s | Status: %s | Attempts: %s",
                delivery.id,
                delivery.status,
                delivery.attempts,
            )

        db.commit()

        return {
            "recovered": recovered_count,
            "dead_letter": dead_letter_count,
        }

    except Exception:
        db.rollback()
        raise


def dispatch_pending_webhook_batch(
    db: Session,
    batch_size: int = WEBHOOK_BATCH_SIZE,
):
    """
    Recover expired leases and dispatch available webhook deliveries.
    """

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    recovery_results = recover_expired_webhook_deliveries(
        db=db,
        batch_size=batch_size,
    )

    statement = (
        select(WebhookDelivery.id)
        .where(
            WebhookDelivery.status == "pending",
            WebhookDelivery.available_at <= datetime.now(timezone.utc),
        )
        .order_by(
            WebhookDelivery.available_at,
            WebhookDelivery.created_at,
        )
        .limit(batch_size)
    )

    delivery_ids = db.execute(statement).scalars().all()

    db.rollback()

    delivered_count = 0
    unsuccessful_count = 0

    for delivery_id in delivery_ids:
        try:
            delivered = dispatch_webhook_delivery(
                db=db,
                delivery_id=delivery_id,
            )

            if delivered:
                delivered_count += 1
            else:
                unsuccessful_count += 1

        except Exception:
            db.rollback()

            unsuccessful_count += 1

            logger.exception(
                "Webhook dispatch failed unexpectedly | Delivery ID: %s",
                delivery_id,
            )

    return {
        "delivered": delivered_count,
        "not_delivered": unsuccessful_count,
        "recovered": recovery_results["recovered"],
        "recovery_dead_letter": recovery_results["dead_letter"],
    }


def process_outbox_batch(
    db: Session,
    batch_size: int = 10,
    kafka_publisher=None,
):
    """
    Process payment and gateway telemetry outbox events.

    Payment business events create webhook deliveries.
    Gateway telemetry events are published to Kafka.

    Database processing and event status updates happen in one
    transaction. Kafka publication occurs before that transaction
    commits, so Redis-side event deduplication is required to handle
    the possible duplicate-publication window.

    HTTP webhook requests happen only after the transaction commits.

    Pass a fresh SQLAlchemy Session with no active transaction.
    """

    processed_count = 0
    retry_count = 0
    failed_count = 0
    enqueued_count = 0
    telemetry_published_count = 0

    with db.begin():

        events = fetch_pending_events(db, batch_size)

        logger.info("Fetched %s pending events", len(events))

        for event in events:
            try:
                with db.begin_nested():
                    handle_outbox_event(event)

                    if event.event_type in SUPPORTED_GATEWAY_EVENTS:
                        publish_gateway_telemetry(
                            event=event,
                            kafka_publisher=kafka_publisher,
                        )
                        telemetry_published_count += 1

                    elif event.event_type in SUPPORTED_PAYMENT_EVENTS:
                        enqueued_count += enqueue_webhook_deliveries(
                            db=db,
                            outbox_event=event,
                        )

                    event.attempts += 1
                    mark_event_published(db, event)

                processed_count += 1

            except Exception:
                logger.exception(
                    "Failed to process event | Event ID: %s",
                    event.id,
                )

                should_retry = schedule_retry(event)

                if should_retry:
                    retry_count += 1
                else:
                    failed_count += 1

    webhook_results = dispatch_pending_webhook_batch(
        db=db,
        batch_size=WEBHOOK_BATCH_SIZE,
    )

    result = {
        "processed": processed_count,
        "retry_scheduled": retry_count,
        "permanently_failed": failed_count,
        "gateway_telemetry_published": telemetry_published_count,
        "webhooks_enqueued": enqueued_count,
        "webhooks_delivered": webhook_results["delivered"],
        "webhooks_not_delivered": webhook_results["not_delivered"],
        "webhooks_recovered": webhook_results["recovered"],
        "webhooks_recovery_dead_letter": webhook_results[
            "recovery_dead_letter"
        ],
    }

    logger.info("Outbox batch completed | Results: %s", result)

    return result
