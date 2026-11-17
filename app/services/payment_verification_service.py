
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.ledger_entry import LedgerEntry
from app.models.ledger_transaction import LedgerTransaction
from app.models.outbox_event import OutboxEvent
from app.models.payment import Payment
from app.providers.base import GatewayError, GatewayPayment
from app.providers.registry import get_gateway_adapter
from app.providers.razorpay import (
    RazorpayError,
    fetch_razorpay_payment,
    verify_razorpay_signature,
)
from app.services.gateway_telemetry_service import enqueue_gateway_attempt


logger = logging.getLogger(__name__)

CLEARING_ACCOUNT_NUMBER = "FUND-1"


class PaymentVerificationError(Exception):
    """Raised when a gateway payment cannot safely be verified or posted."""


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _record_gateway_attempt(
    db,
    payment,
    provider,
    outcome,
    latency_ms,
    error=None,
):
    """Persist one provider API attempt without blocking payment processing."""
    try:
        enqueue_gateway_attempt(
            db,
            payment_id=payment.id,
            gateway=provider,
            outcome=outcome,
            latency_ms=latency_ms,
            payment_method=getattr(payment, "payment_method", None),
            bank_code=getattr(payment, "bank_code", None),
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            error=str(error)[:500] if error else None,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Could not persist gateway telemetry for payment %s",
            payment.id,
        )


def _execute_gateway_request(db, payment, provider, request):
    """Measure a provider API request and record its outcome."""
    started_at = time.perf_counter()

    try:
        result = request()
    except Exception as exc:
        latency_ms = (time.perf_counter() - started_at) * 1000
        outcome = (
            "timeout"
            if isinstance(exc, (TimeoutError, httpx.TimeoutException))
            else "failure"
        )

        _record_gateway_attempt(
            db,
            payment,
            provider,
            outcome,
            latency_ms,
            error=exc,
        )
        raise

    latency_ms = (time.perf_counter() - started_at) * 1000

    _record_gateway_attempt(
        db,
        payment,
        provider,
        "success",
        latency_ms,
    )

    return result


def _normalize_gateway_payment(gateway_payment):
    """Convert provider-specific payment data into one common dictionary."""
    if isinstance(gateway_payment, GatewayPayment):
        return {
            "provider": gateway_payment.provider,
            "id": gateway_payment.provider_payment_id,
            "order_id": gateway_payment.provider_order_id,
            "amount": gateway_payment.amount_minor,
            "currency": gateway_payment.currency,
            "status": gateway_payment.status,
            "captured": gateway_payment.captured,
        }

    if isinstance(gateway_payment, dict):
        return gateway_payment

    raise PaymentVerificationError(
        "The payment gateway returned an unsupported payment response."
    )


def _validate_gateway_payment(gateway_payment, payment):
    gateway_payment = _normalize_gateway_payment(gateway_payment)

    provider = _enum_value(payment.provider)

    if gateway_payment.get("provider") and (
        gateway_payment["provider"].lower() != provider.lower()
    ):
        raise PaymentVerificationError(
            "Payment gateway provider does not match the LedgerCore payment."
        )

    if gateway_payment.get("order_id") != payment.provider_order_id:
        raise PaymentVerificationError(
            "Gateway order ID does not match the LedgerCore payment."
        )

    if gateway_payment.get("amount") != payment.amount_minor:
        raise PaymentVerificationError(
            "Gateway payment amount does not match the LedgerCore payment."
        )

    if gateway_payment.get("currency") != payment.currency:
        raise PaymentVerificationError(
            "Gateway payment currency does not match the LedgerCore payment."
        )

    if str(gateway_payment.get("status", "")).lower() not in (
        "captured",
        "success",
    ):
        raise PaymentVerificationError(
            "Gateway payment has not been successfully captured."
        )

    if gateway_payment.get("captured") is False:
        raise PaymentVerificationError(
            "Gateway reports that this payment is not captured."
        )

    if not gateway_payment.get("id"):
        raise PaymentVerificationError(
            "Gateway did not return a payment ID."
        )

    return gateway_payment


def _validate_recovery_payment(
    gateway_payment,
    payment,
    requested_payment_id,
):
    gateway_payment = _validate_gateway_payment(
        gateway_payment,
        payment,
    )

    if (
        requested_payment_id
        and gateway_payment.get("id") != requested_payment_id
    ):
        raise PaymentVerificationError(
            "Gateway returned a different payment ID than requested."
        )

    return gateway_payment


def _calculate_new_balance(account, side, amount_minor):
    normal_side = _enum_value(account.normal_side)

    if side == normal_side:
        new_balance = account.current_balance_minor + amount_minor
    else:
        new_balance = account.current_balance_minor - amount_minor

    if new_balance < 0 and not account.allow_negative:
        raise PaymentVerificationError(
            f"Insufficient balance in account {account.account_number}."
        )

    return new_balance


def _get_existing_payment_transaction(db, payment_id):
    return db.execute(
        select(LedgerTransaction).where(
            LedgerTransaction.type == "payment",
            LedgerTransaction.reference_id == payment_id,
        )
    ).scalar_one_or_none()


def _validate_existing_ledger(db, transaction, payment):
    entries = db.execute(
        select(LedgerEntry).where(
            LedgerEntry.transaction_id == transaction.id
        )
    ).scalars().all()

    if len(entries) != 2:
        raise PaymentVerificationError(
            "Existing payment ledger transaction is incomplete."
        )

    debits = [entry for entry in entries if entry.side == "debit"]
    credits = [entry for entry in entries if entry.side == "credit"]

    if len(debits) != 1 or len(credits) != 1:
        raise PaymentVerificationError(
            "Existing payment ledger transaction is not balanced."
        )

    if (
        debits[0].amount_minor <= 0
        or debits[0].amount_minor != credits[0].amount_minor
        or debits[0].amount_minor != payment.amount_minor
    ):
        raise PaymentVerificationError(
            "Existing payment ledger entries have invalid amounts."
        )


def _enqueue_payment_succeeded_event(db, payment, provider_payment_id):
    """
    Add the payment success event to the current database transaction.

    The caller commits this event together with the ledger entries and
    payment status update.
    """
    provider = _enum_value(payment.provider).lower()

    event = OutboxEvent(
        aggregate_type="payment",
        aggregate_id=payment.id,
        event_type="payment.succeeded",
        payload={
            "payment_id": str(payment.id),
            "merchant_account_id": str(payment.merchant_account_id),
            "customer_account_id": (
                str(payment.customer_account_id)
                if payment.customer_account_id
                else None
            ),
            "provider": provider,
            "provider_payment_id": provider_payment_id,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "status": "succeeded",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        status="pending",
    )

    db.add(event)


def _post_captured_payment(
    db,
    payment_id,
    provider_payment_id,
    gateway_payment=None,
):
    """
    Post an authenticated and validated captured payment.

    Ledger entries, payment status, and payment.succeeded outbox event
    are committed atomically.
    """
    try:
        payment = db.execute(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
        ).scalar_one_or_none()

        if payment is None:
            raise PaymentVerificationError("Payment not found.")

        provider = _enum_value(payment.provider).lower()

        if payment.provider_payment_id:
            if payment.provider_payment_id != provider_payment_id:
                raise PaymentVerificationError(
                    "A different gateway payment ID is already associated "
                    "with this LedgerCore payment."
                )

        if payment.status == "succeeded":
            if payment.provider_payment_id != provider_payment_id:
                raise PaymentVerificationError(
                    "Payment was already completed using a different "
                    "gateway payment ID."
                )

            transaction = _get_existing_payment_transaction(
                db, payment.id
            )

            if transaction is None:
                raise PaymentVerificationError(
                    "Payment is marked succeeded but its ledger transaction "
                    "is missing."
                )

            _validate_existing_ledger(db, transaction, payment)
            db.commit()

            return {
                "status": "succeeded",
                "already_processed": True,
                "payment_id": str(payment.id),
            }

        if payment.status not in ("pending", "processing"):
            raise PaymentVerificationError(
                f"Payment cannot be posted from status '{payment.status}'."
            )

        if gateway_payment is None:
            raise PaymentVerificationError(
                "Verified gateway payment details are required."
            )

        gateway_payment = _validate_gateway_payment(
            gateway_payment,
            payment,
        )

        if gateway_payment.get("id") != provider_payment_id:
            raise PaymentVerificationError(
                "Gateway payment ID does not match the payment being posted."
            )

        reused_payment = db.execute(
            select(Payment).where(
                Payment.provider == payment.provider,
                Payment.provider_payment_id == provider_payment_id,
                Payment.id != payment.id,
            )
        ).scalar_one_or_none()

        if reused_payment is not None:
            raise PaymentVerificationError(
                "This gateway payment ID is already associated with "
                "another LedgerCore payment."
            )

        existing_transaction = _get_existing_payment_transaction(
            db, payment.id
        )

        if existing_transaction is not None:
            raise PaymentVerificationError(
                "A ledger transaction already exists for this payment, "
                "but the payment is not marked succeeded. Manual review "
                "is required."
            )

        accounts = db.execute(
            select(Account)
            .where(
                Account.account_number.in_([CLEARING_ACCOUNT_NUMBER])
                | (Account.id == payment.merchant_account_id)
            )
            .order_by(Account.id)
            .with_for_update()
        ).scalars().all()

        account_by_number = {
            account.account_number: account for account in accounts
        }

        clearing_account = account_by_number.get(
            CLEARING_ACCOUNT_NUMBER
        )

        merchant_account = next(
            (
                account
                for account in accounts
                if account.id == payment.merchant_account_id
            ),
            None,
        )

        if clearing_account is None:
            raise PaymentVerificationError(
                f"Clearing account {CLEARING_ACCOUNT_NUMBER} does not exist."
            )

        if merchant_account is None:
            raise PaymentVerificationError(
                "Merchant account does not exist."
            )

        if clearing_account.id == merchant_account.id:
            raise PaymentVerificationError(
                "Clearing and merchant accounts must be different."
            )

        if (
            clearing_account.currency != payment.currency
            or merchant_account.currency != payment.currency
        ):
            raise PaymentVerificationError(
                "Payment and ledger account currencies do not match."
            )

        if (
            _enum_value(clearing_account.type) != "asset"
            or _enum_value(clearing_account.normal_side) != "debit"
        ):
            raise PaymentVerificationError(
                "Clearing account must be an asset with debit normal balance."
            )

        if (
            _enum_value(merchant_account.type) != "liability"
            or _enum_value(merchant_account.normal_side) != "credit"
        ):
            raise PaymentVerificationError(
                "Merchant account must be a liability with credit "
                "normal balance."
            )

        if payment.amount_minor <= 0:
            raise PaymentVerificationError(
                "Payment amount must be positive."
            )

        _calculate_new_balance(
            clearing_account,
            "debit",
            payment.amount_minor,
        )

        _calculate_new_balance(
            merchant_account,
            "credit",
            payment.amount_minor,
        )

        transaction = LedgerTransaction(
            payment_id=payment.id,
            type="payment",
            reference_id=payment.id,
            currency=payment.currency,
            description=f"{provider.title()} payment captured",
        )

        db.add(transaction)
        db.flush()

        db.add_all(
            [
                LedgerEntry(
                    transaction_id=transaction.id,
                    account_id=clearing_account.id,
                    side="debit",
                    amount_minor=payment.amount_minor,
                ),
                LedgerEntry(
                    transaction_id=transaction.id,
                    account_id=merchant_account.id,
                    side="credit",
                    amount_minor=payment.amount_minor,
                ),
            ]
        )

        payment.provider_payment_id = provider_payment_id
        payment.status = "succeeded"

        _enqueue_payment_succeeded_event(
            db,
            payment,
            provider_payment_id,
        )

        db.commit()

        return {
            "status": "succeeded",
            "already_processed": False,
            "payment_id": str(payment.id),
        }

    except PaymentVerificationError:
        db.rollback()
        raise

    except IntegrityError as exc:
        db.rollback()
        raise PaymentVerificationError(
            "A database constraint prevented payment posting. "
            "The payment was not posted."
        ) from exc

    except Exception:
        db.rollback()
        raise


def _fetch_provider_payment(db, payment, provider):
    """Fetch authoritative transaction details from a supported gateway."""
    if not payment.provider_order_id:
        raise PaymentVerificationError(
            f"Payment does not have a {provider.title()} order ID."
        )

    try:
        adapter = get_gateway_adapter(provider)

        return _execute_gateway_request(
            db,
            payment,
            provider,
            lambda: adapter.fetch_payment(payment.provider_order_id),
        )

    except GatewayError as exc:
        raise PaymentVerificationError(
            f"Could not retrieve payment details from {provider.title()}."
        ) from exc

    except PaymentVerificationError:
        raise

    except Exception as exc:
        raise PaymentVerificationError(
            f"Could not retrieve payment details from {provider.title()}."
        ) from exc


def _fetch_razorpay_payment(db, payment, razorpay_payment_id):
    try:
        return _execute_gateway_request(
            db,
            payment,
            "razorpay",
            lambda: fetch_razorpay_payment(razorpay_payment_id),
        )

    except RazorpayError as exc:
        raise PaymentVerificationError(
            "Could not retrieve payment details from Razorpay."
        ) from exc

    except PaymentVerificationError:
        raise

    except Exception as exc:
        raise PaymentVerificationError(
            "Could not retrieve payment details from Razorpay."
        ) from exc


def verify_and_post_payment(
    db: Session,
    payment_id: uuid.UUID,
    razorpay_order_id: str | None = None,
    razorpay_payment_id: str | None = None,
    razorpay_signature: str | None = None,
):
    """Verify a payment with its selected gateway and post it to the ledger."""
    payment = db.execute(
        select(Payment).where(Payment.id == payment_id)
    ).scalar_one_or_none()

    if payment is None:
        raise PaymentVerificationError("Payment not found.")

    provider = _enum_value(payment.provider).lower()

    if not payment.provider_order_id:
        raise PaymentVerificationError(
            "Payment does not have a gateway order ID."
        )

    if payment.amount_minor <= 0:
        raise PaymentVerificationError(
            "Payment amount must be positive."
        )

    if payment.status == "succeeded":
        if not payment.provider_payment_id:
            raise PaymentVerificationError(
                "Succeeded payment has no associated gateway payment ID."
            )

        return _post_captured_payment(
            db,
            payment_id,
            payment.provider_payment_id,
        )

    if payment.status not in ("pending", "processing"):
        raise PaymentVerificationError(
            f"Payment cannot be verified from status '{payment.status}'."
        )

    if provider == "razorpay":
        if not razorpay_order_id or not razorpay_payment_id:
            raise PaymentVerificationError(
                "Razorpay order ID and payment ID are required."
            )

        if not razorpay_signature:
            raise PaymentVerificationError(
                "Razorpay Checkout signature is required."
            )

        if payment.provider_order_id != razorpay_order_id:
            raise PaymentVerificationError(
                "Razorpay order ID does not match the LedgerCore payment."
            )

        if not verify_razorpay_signature(
            razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature,
        ):
            raise PaymentVerificationError(
                "Invalid Razorpay signature."
            )

        db.rollback()

        gateway_payment = _fetch_razorpay_payment(
            db,
            payment,
            razorpay_payment_id,
        )

        gateway_payment = _normalize_gateway_payment(
            gateway_payment
        )
        gateway_payment["provider"] = "razorpay"

        gateway_payment = _validate_gateway_payment(
            gateway_payment,
            payment,
        )

        if gateway_payment.get("id") != razorpay_payment_id:
            raise PaymentVerificationError(
                "Razorpay returned a different payment ID than expected."
            )

        return _post_captured_payment(
            db,
            payment_id,
            razorpay_payment_id,
            gateway_payment,
        )

    if provider in ("cashfree", "payu"):
        db.rollback()

        gateway_payment = _fetch_provider_payment(
            db,
            payment,
            provider,
        )

        gateway_payment = _normalize_gateway_payment(
            gateway_payment
        )

        gateway_payment = _validate_gateway_payment(
            gateway_payment,
            payment,
        )

        return _post_captured_payment(
            db,
            payment_id,
            gateway_payment["id"],
            gateway_payment,
        )

    raise PaymentVerificationError(
        f"Payment verification is not implemented for provider '{provider}'."
    )


def recover_and_post_payment(
    db: Session,
    payment_id: uuid.UUID,
    razorpay_payment_id: str | None = None,
):
    """Recover a payment using authenticated server-to-server gateway APIs."""
    payment = db.execute(
        select(Payment).where(Payment.id == payment_id)
    ).scalar_one_or_none()

    if payment is None:
        raise PaymentVerificationError("Payment not found.")

    provider = _enum_value(payment.provider).lower()

    if not payment.provider_order_id:
        raise PaymentVerificationError(
            "Payment does not have a gateway order ID."
        )

    if payment.amount_minor <= 0:
        raise PaymentVerificationError(
            "Payment amount must be positive."
        )

    if payment.status == "succeeded":
        if not payment.provider_payment_id:
            raise PaymentVerificationError(
                "Succeeded payment has no associated gateway payment ID."
            )

        if (
            razorpay_payment_id
            and payment.provider_payment_id != razorpay_payment_id
        ):
            raise PaymentVerificationError(
                "Payment was already completed using a different "
                "gateway payment ID."
            )

        return _post_captured_payment(
            db,
            payment_id,
            payment.provider_payment_id,
        )

    if payment.status not in ("pending", "processing"):
        raise PaymentVerificationError(
            f"Payment cannot be recovered from status '{payment.status}'."
        )

    db.rollback()

    if provider == "razorpay":
        if not razorpay_payment_id or not razorpay_payment_id.strip():
            raise PaymentVerificationError(
                "A Razorpay payment ID is required."
            )

        gateway_payment = _fetch_razorpay_payment(
            db,
            payment,
            razorpay_payment_id,
        )

        gateway_payment = _normalize_gateway_payment(
            gateway_payment
        )
        gateway_payment["provider"] = "razorpay"

        gateway_payment = _validate_recovery_payment(
            gateway_payment,
            payment,
            razorpay_payment_id,
        )

    elif provider in ("cashfree", "payu"):
        gateway_payment = _fetch_provider_payment(
            db,
            payment,
            provider,
        )

        gateway_payment = _normalize_gateway_payment(
            gateway_payment
        )

        gateway_payment = _validate_recovery_payment(
            gateway_payment,
            payment,
            None,
        )

    else:
        raise PaymentVerificationError(
            f"Payment recovery is not implemented for provider '{provider}'."
        )

    return _post_captured_payment(
        db,
        payment_id,
        gateway_payment["id"],
        gateway_payment,
    )
