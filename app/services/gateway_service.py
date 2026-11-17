
import logging
import time
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import Account
from app.models.payment import Payment
from app.providers.base import GatewayError, GatewayOrder
from app.providers.registry import get_gateway_adapter
from app.services.aggregator_client import (
    AggregatorError,
    create_aggregated_gateway_order,
)
from app.services.gateway_telemetry_service import enqueue_gateway_attempt
from app.services.merchant_profile_service import (
    get_merchant_profile,
    get_merchant_routing_preferences,
)


logger = logging.getLogger(__name__)

REAL_AGGREGATOR_PROVIDERS = {
    "razorpay",
    "cashfree",
    "payu",
}


class GatewayServiceError(Exception):
    pass


def _create_order_with_telemetry(
    db: Session,
    payment: Payment,
    adapter,
    provider: str,
    **kwargs,
) -> GatewayOrder:
    started_at = time.perf_counter()

    try:
        gateway_order = adapter.create_order(**kwargs)
        latency_ms = (time.perf_counter() - started_at) * 1000

        enqueue_gateway_attempt(
            db,
            payment_id=payment.id,
            gateway=provider,
            outcome="success",
            latency_ms=latency_ms,
            payment_method=getattr(payment, "payment_method", None),
            bank_code=getattr(payment, "bank_code", None),
            amount_minor=payment.amount_minor,
            currency=payment.currency,
        )

        return gateway_order

    except Exception as exc:
        latency_ms = (time.perf_counter() - started_at) * 1000

        outcome = (
            "timeout"
            if isinstance(exc, (TimeoutError, httpx.TimeoutException))
            else "failure"
        )

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
                error=str(exc)[:500],
            )
            db.commit()

        except Exception:
            db.rollback()
            logger.exception(
                "Failed to persist gateway failure telemetry | "
                "Payment ID: %s | Gateway: %s",
                payment.id,
                provider,
            )

        raise


def _get_routing_preferences(
    db: Session,
    payment: Payment,
) -> tuple[list[str], str | None]:
    current_provider = (payment.provider or "").strip().lower()

    if current_provider != "auto":
        if current_provider not in REAL_AGGREGATOR_PROVIDERS:
            raise GatewayServiceError(
                f"Unsupported payment provider: {current_provider}"
            )

        return [current_provider], None

    merchant_profile = get_merchant_profile(
        db,
        payment.merchant_account_id,
    )

    enabled_gateways, preferred_gateway = (
        get_merchant_routing_preferences(merchant_profile)
    )

    allowed = [
        provider.strip().lower()
        for provider in enabled_gateways
        if isinstance(provider, str)
        and provider.strip().lower() in REAL_AGGREGATOR_PROVIDERS
    ]

    allowed = list(dict.fromkeys(allowed))

    if not allowed:
        raise GatewayServiceError(
            "Merchant has no eligible real payment gateways enabled"
        )

    if preferred_gateway:
        preferred_gateway = preferred_gateway.strip().lower()

        if preferred_gateway not in allowed:
            preferred_gateway = None

    return allowed, preferred_gateway


def _create_aggregator_order(
    payment: Payment,
    customer_name: str | None,
    allowed_providers: list[str],
    preferred_provider: str | None,
) -> GatewayOrder:
    stable_idempotency_key = (
        f"{payment.id}:{payment.request_hash}"
    )

    try:
        aggregator_response = create_aggregated_gateway_order(
            payment_id=payment.id,
            merchant_id=payment.merchant_account_id,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            idempotency_key=stable_idempotency_key,
            allowed_providers=allowed_providers,
            preferred_provider=preferred_provider,
            customer_phone=payment.customer_phone,
            customer_email=payment.customer_email,
            customer_name=customer_name,
            payment_method=getattr(payment, "payment_method", None),
            bank_code=getattr(payment, "bank_code", None),
        )

    except AggregatorError as exc:
        if exc.outcome_unknown:
            logger.error(
                "Unknown aggregator payment outcome. "
                "Payment ID: %s. Reconciliation is required.",
                payment.id,
            )

            raise GatewayServiceError(
                "Payment order creation outcome is unknown. "
                "Reconcile the provider order before retrying checkout."
            ) from exc

        raise GatewayServiceError(
            f"Payment aggregator failed: {exc}"
        ) from exc

    result = aggregator_response["result"]
    selected_provider = str(result["provider"]).strip().lower()

    if selected_provider not in allowed_providers:
        raise GatewayServiceError(
            "Aggregator selected a provider not enabled for this merchant"
        )

    checkout_data = result.get("checkout_data") or {}

    return GatewayOrder(
        provider=selected_provider,
        provider_order_id=result["provider_order_id"],
        amount_minor=int(result["amount_minor"]),
        currency=result["currency"].upper(),
        provider_session_id=result.get("provider_session_id"),
        checkout_data=checkout_data,
    )


def create_gateway_order(
    db: Session,
    payment_id: UUID,
) -> dict:
    try:
        # 1. Load payment.
        payment = db.get(Payment, payment_id)

        if payment is None:
            raise GatewayServiceError("Payment not found")

        if payment.status != "pending":
            raise GatewayServiceError(
                f"Cannot create gateway order for payment "
                f"with status: {payment.status}"
            )

        original_provider = (payment.provider or "").strip().lower()

        # 2. Load customer account.
        customer = db.get(
            Account,
            payment.customer_account_id,
        )

        if customer is None:
            raise GatewayServiceError("Customer account not found")

        customer_name = getattr(customer, "name", None)

        # 3. Reuse an existing provider order; never reroute it.
        if payment.provider_order_id:
            if not original_provider or original_provider == "auto":
                raise GatewayServiceError(
                    "Existing provider order has no resolved provider"
                )

            adapter = get_gateway_adapter(original_provider)

            if original_provider == "razorpay":
                if not settings.RAZORPAY_KEY_ID:
                    raise GatewayServiceError(
                        "Razorpay Key ID is not configured"
                    )

                gateway_order = GatewayOrder(
                    provider="razorpay",
                    provider_order_id=payment.provider_order_id,
                    amount_minor=payment.amount_minor,
                    currency=payment.currency.upper(),
                    provider_session_id=payment.provider_session_id,
                    checkout_data={
                        "key_id": settings.RAZORPAY_KEY_ID,
                    },
                )

            elif original_provider == "payu":
                if not payment.customer_email or not payment.customer_phone:
                    raise GatewayServiceError(
                        "Customer email and phone are required for PayU"
                    )

                gateway_order = _create_order_with_telemetry(
                    db=db,
                    payment=payment,
                    adapter=adapter,
                    provider=original_provider,
                    payment_id=str(payment.id),
                    amount_minor=payment.amount_minor,
                    currency=payment.currency,
                    customer_phone=payment.customer_phone,
                    customer_email=payment.customer_email,
                    customer_name=customer_name,
                )

                if (
                    gateway_order.provider_order_id
                    != payment.provider_order_id
                ):
                    raise GatewayServiceError(
                        "PayU transaction ID does not match "
                        "the existing payment order"
                    )

            else:
                gateway_order = _create_order_with_telemetry(
                    db=db,
                    payment=payment,
                    adapter=adapter,
                    provider=original_provider,
                    payment_id=str(payment.id),
                    amount_minor=payment.amount_minor,
                    currency=payment.currency,
                    customer_phone=payment.customer_phone,
                )

                if (
                    gateway_order.provider_order_id
                    != payment.provider_order_id
                ):
                    raise GatewayServiceError(
                        "Gateway returned a different order ID "
                        "for an existing payment"
                    )

        else:
            # 4. Apply merchant restrictions and preference.
            allowed_providers, preferred_provider = (
                _get_routing_preferences(db, payment)
            )

            # 5. Let the aggregator select and execute the provider.
            gateway_order = _create_aggregator_order(
                payment=payment,
                customer_name=customer_name,
                allowed_providers=allowed_providers,
                preferred_provider=preferred_provider,
            )

            payment.provider = gateway_order.provider
            payment.provider_order_id = gateway_order.provider_order_id
            payment.provider_session_id = gateway_order.provider_session_id

        # 6. Persist order details.
        db.commit()
        db.refresh(payment)

        provider = payment.provider.strip().lower()
        checkout_data = gateway_order.checkout_data or {}

        # 7. Common response.
        response = {
            "payment_id": str(payment.id),
            "provider": provider,
            "provider_order_id": payment.provider_order_id,
            "provider_session_id": payment.provider_session_id,
            "amount_minor": payment.amount_minor,
            "currency": payment.currency,
            "status": payment.status,
            "checkout_data": checkout_data,
        }

        # 8. Provider-specific checkout fields.
        if provider == "razorpay":
            response["key_id"] = checkout_data.get("key_id")

        elif provider == "cashfree":
            response["payment_session_id"] = payment.provider_session_id
            response["environment"] = checkout_data.get("environment")

        elif provider == "payu":
            response["checkout_url"] = checkout_data.get("checkout_url") or checkout_data.get("payment_url")
            response["method"] = checkout_data.get("method")
            response["fields"] = checkout_data.get("fields")
            response["environment"] = checkout_data.get("environment")

        return response

    except GatewayServiceError:
        db.rollback()
        raise

    except (GatewayError, AggregatorError) as exc:
        db.rollback()
        raise GatewayServiceError(str(exc)) from exc

    except Exception as exc:
        db.rollback()
        raise GatewayServiceError(
            f"Failed to create gateway order: {exc}"
        ) from exc
