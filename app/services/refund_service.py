
import hashlib
import json

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.refund import Refund


class RefundError(Exception):
    """Base exception for refund operations."""


class RefundPaymentNotFoundError(RefundError):
    pass


class RefundPaymentNotEligibleError(RefundError):
    pass


class RefundIdempotencyConflictError(RefundError):
    pass


class RefundAmountExceededError(RefundError):
    pass


def _status_value(status) -> str:
    return str(getattr(status, "value", status)).lower()


def _build_request_hash(
    payment_id,
    amount_minor: int,
    reason: str | None,
) -> str:
    payload = {
        "payment_id": str(payment_id),
        "amount_minor": amount_minor,
        "reason": reason,
    }

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def create_refund_intent(
    db: Session,
    *,
    merchant_account_id,
    payment_id,
    amount_minor: int,
    idempotency_key: str,
    reason: str | None = None,
) -> Refund:
    """
    Reserve a refund amount before contacting the payment provider.

    Pending, processing, succeeded and needs_review refunds reserve
    their amounts. Only definitively failed refunds release the amount.
    """

    if amount_minor <= 0:
        raise ValueError("Refund amount must be greater than zero.")

    idempotency_key = idempotency_key.strip()

    if not idempotency_key or len(idempotency_key) > 255:
        raise ValueError("Invalid Idempotency-Key.")

    request_hash = _build_request_hash(
        payment_id,
        amount_minor,
        reason,
    )

    try:
        # Serialize refund reservations for this payment.
        payment = (
            db.query(Payment)
            .filter(
                Payment.id == payment_id,
                Payment.merchant_account_id == merchant_account_id,
            )
            .with_for_update()
            .first()
        )

        if payment is None:
            raise RefundPaymentNotFoundError(
                "Payment not found."
            )

        # Check idempotency before creating another reservation.
        existing = (
            db.query(Refund)
            .filter(
                Refund.merchant_account_id == merchant_account_id,
                Refund.idempotency_key == idempotency_key,
            )
            .first()
        )

        if existing is not None:
            if (
                existing.request_hash != request_hash
                or existing.payment_id != payment_id
            ):
                raise RefundIdempotencyConflictError(
                    "Idempotency-Key was already used for a different refund request."
                )

            db.commit()
            return existing

        if _status_value(payment.status) != "succeeded":
            raise RefundPaymentNotEligibleError(
                "Only successfully completed payments can be refunded."
            )

        if not payment.provider:
            raise RefundPaymentNotEligibleError(
                "Payment has no associated payment provider."
            )

        reserved_amount = (
            db.query(func.coalesce(func.sum(Refund.amount_minor), 0))
            .filter(
                Refund.payment_id == payment_id,
                Refund.status != "failed",
            )
            .scalar()
        )

        if reserved_amount + amount_minor > payment.amount_minor:
            raise RefundAmountExceededError(
                "Requested refund exceeds the remaining refundable amount."
            )

        refund = Refund(
            payment_id=payment.id,
            merchant_account_id=merchant_account_id,
            amount_minor=amount_minor,
            currency=payment.currency,
            provider=payment.provider,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="pending",
            response={"reason": reason} if reason else None,
        )

        db.add(refund)
        db.commit()
        db.refresh(refund)

        return refund

    except IntegrityError:
        db.rollback()

        # Another concurrent request may have inserted the same key.
        existing = (
            db.query(Refund)
            .filter(
                Refund.merchant_account_id == merchant_account_id,
                Refund.idempotency_key == idempotency_key,
            )
            .first()
        )

        if existing is not None:
            if (
                existing.request_hash == request_hash
                and existing.payment_id == payment_id
            ):
                return existing

            raise RefundIdempotencyConflictError(
                "Idempotency-Key was already used for a different refund request."
            )

        raise

    except Exception:
        db.rollback()
        raise


def list_payment_refunds(
    db: Session,
    *,
    merchant_account_id,
    payment_id,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Refund], int]:
    """Return refunds for a payment owned by the authenticated merchant."""

    payment_exists = (
        db.query(Payment.id)
        .filter(
            Payment.id == payment_id,
            Payment.merchant_account_id == merchant_account_id,
        )
        .first()
    )

    if payment_exists is None:
        raise RefundPaymentNotFoundError("Payment not found.")

    query = (
        db.query(Refund)
        .filter(
            Refund.payment_id == payment_id,
            Refund.merchant_account_id == merchant_account_id,
        )
    )

    total = query.count()

    items = (
        query.order_by(Refund.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return items, total
