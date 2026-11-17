
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select

from app.models.payment import Payment
from app.models.idempotency_key import IdempotencyKey
from app.models.outbox_event import OutboxEvent


def test_50_concurrent_identical_requests_create_one_payment(
    client,
    accounts,
    db_session,
):
    request_count = 50
    idempotency_key = "concurrency-test-001"

    payload = {
        "customer_account_id": str(accounts["customer"].id),
        "amount_minor": 10000,
        "currency": "INR",
    }

    # Make all 50 requests start together.
    barrier = Barrier(request_count)

    def send_request():
        barrier.wait(timeout=30)

        return client.post(
            "/api/v1/payments",
            json=payload,
            headers={
                "Idempotency-Key": idempotency_key,
            },
        )

    # Send 50 simultaneous HTTP requests.
    with ThreadPoolExecutor(max_workers=request_count) as executor:
        futures = [
            executor.submit(send_request)
            for _ in range(request_count)
        ]

        responses = [
            future.result(timeout=90)
            for future in futures
        ]

    # Every request should succeed.
    assert all(
        response.status_code == 201
        for response in responses
    ), [
        {
            "status": response.status_code,
            "body": response.text,
        }
        for response in responses
        if response.status_code != 201
    ]

    # All requests must return the same payment ID.
    payment_ids = {
        response.json()["id"]
        for response in responses
    }

    assert len(payment_ids) == 1

    # Exactly one payment must exist.
    payment_count = db_session.scalar(
        select(func.count()).select_from(Payment)
    )

    assert payment_count == 1

    # Exactly one idempotency record must exist.
    idempotency_count = db_session.scalar(
        select(func.count()).select_from(IdempotencyKey)
    )

    assert idempotency_count == 1

    # Exactly one payment.created outbox event must exist.
    outbox_count = db_session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.event_type == "payment.created"
        )
    )

    assert outbox_count == 1

    # Verify the stored payment.
    payment = db_session.get(
        Payment,
        next(iter(payment_ids)),
    )

    assert payment is not None
    assert payment.amount_minor == 10000
    assert payment.currency == "INR"
    assert payment.status == "pending"
