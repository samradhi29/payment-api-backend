
import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.payment import Payment
from app.models.idempotency_key import IdempotencyKey
from app.models.outbox_event import OutboxEvent
from app.schemas.payment import PaymentCreate
from app.services.merchant_profile_service import (
    get_merchant_profile,
    get_merchant_routing_preferences,
    validate_merchant_gateway,
)


class IdempotencyConflictError(Exception):
    pass


def create_payment(
    db: Session,
    merchant_account_id: UUID,
    payment_data: PaymentCreate,
    idempotency_key: str,
) -> Payment:
    try:
        # 1. Verify authenticated merchant account.
        merchant = db.get(Account, merchant_account_id)

        if merchant is None:
            raise ValueError("Merchant account not found")

        # 2. Verify customer account.
        customer = db.get(
            Account,
            payment_data.customer_account_id,
        )

        if customer is None:
            raise ValueError("Customer account not found")

        if customer.id == merchant.id:
            raise ValueError(
                "Customer and merchant accounts must be different"
            )

        # 3. Verify currencies.
        if customer.currency != payment_data.currency:
            raise ValueError("Customer account currency mismatch")

        if merchant.currency != payment_data.currency:
            raise ValueError("Merchant account currency mismatch")

        # 4. Hash the original request.
        request_payload = {
            "customer_account_id": str(
                payment_data.customer_account_id
            ),
            "merchant_account_id": str(merchant_account_id),
            "amount_minor": payment_data.amount_minor,
            "currency": payment_data.currency,
            "provider": payment_data.provider,
            "payment_method": payment_data.payment_method,
            "bank_code": payment_data.bank_code,
            "customer_phone": payment_data.customer_phone,
            "customer_email": payment_data.customer_email,
        }

        canonical_payload = json.dumps(
            request_payload,
            sort_keys=True,
            separators=(",", ":"),
        )

        request_hash = hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest()

        # 5. Atomically claim the merchant-scoped idempotency key.
        statement = (
            insert(IdempotencyKey)
            .values(
                scope_type="merchant",
                scope_id=merchant_account_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "scope_type",
                    "scope_id",
                    "idempotency_key",
                ]
            )
            .returning(IdempotencyKey.id)
        )

        result = db.execute(statement)
        inserted_id = result.scalar_one_or_none()

        # 6. Return the original payment for a matching duplicate.
        if inserted_id is None:
            existing = db.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.scope_type == "merchant",
                    IdempotencyKey.scope_id == merchant_account_id,
                    IdempotencyKey.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()

            if existing is None:
                raise RuntimeError(
                    "Idempotency record could not be retrieved"
                )

            if existing.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "Idempotency key already used with a different request"
                )

            if existing.payment_id is None:
                raise RuntimeError(
                    "Existing idempotency record has no payment"
                )

            payment = db.get(Payment, existing.payment_id)

            if payment is None:
                raise RuntimeError("Original payment not found")

            db.rollback()
            return payment

        # 7. Validate merchant gateway configuration.
        merchant_profile = get_merchant_profile(
            db,
            merchant_account_id,
        )

        requested_provider = payment_data.provider

        if requested_provider == "auto":
            enabled_gateways, preferred_gateway = (
                get_merchant_routing_preferences(merchant_profile)
            )

            if not enabled_gateways:
                raise ValueError(
                    "Merchant has no enabled payment gateways"
                )

            # The aggregator will apply the merchant's gateway
            # restrictions when the provider order is created.
        else:
            validate_merchant_gateway(
                merchant_profile,
                requested_provider,
            )

        # 8. Create payment.
        # Keep "auto" until the aggregator actually executes the order.
        initial_provider = (
            "auto"
            if requested_provider == "auto"
            else requested_provider
        )

        payment = Payment(
            customer_account_id=payment_data.customer_account_id,
            merchant_account_id=merchant_account_id,
            amount_minor=payment_data.amount_minor,
            currency=payment_data.currency,
            provider=initial_provider,
            payment_method=payment_data.payment_method,
            bank_code=payment_data.bank_code,
            customer_phone=payment_data.customer_phone,
            customer_email=payment_data.customer_email,
            status="pending",
            request_hash=request_hash,
        )

        db.add(payment)
        db.flush()

        # 9. Create the transactional outbox event.
        outbox_event = OutboxEvent(
            aggregate_type="payment",
            aggregate_id=payment.id,
            event_type="payment.created",
            payload={
                "payment_id": str(payment.id),
                "customer_account_id": str(
                    payment.customer_account_id
                ),
                "merchant_account_id": str(
                    payment.merchant_account_id
                ),
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "provider": payment.provider,
                "status": payment.status,
            },
            status="pending",
        )

        db.add(outbox_event)

        # 10. Link the idempotency record to the payment.
        idempotency_record = db.get(
            IdempotencyKey,
            inserted_id,
        )

        if idempotency_record is None:
            raise RuntimeError(
                "New idempotency record could not be retrieved"
            )

        idempotency_record.payment_id = payment.id
        idempotency_record.response_status = 201
        idempotency_record.response_body = {
            "id": str(payment.id),
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "provider": payment.provider,
            "payment_method": payment.payment_method,
            "bank_code": payment.bank_code,
            "status": payment.status,
        }

        # 11. Commit all records atomically.
        db.commit()
        db.refresh(payment)

        return payment

    except Exception:
        db.rollback()
        raise
