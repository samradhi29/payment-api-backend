
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.providers.base import (
    GatewayError,
    GatewayOrder,
    GatewayPayment,
    PaymentGatewayAdapter,
)


CASHFREE_SANDBOX_URL = "https://sandbox.cashfree.com/pg"
CASHFREE_PRODUCTION_URL = "https://api.cashfree.com/pg"

REQUEST_TIMEOUT = 15.0


class CashfreeError(GatewayError):
    """Raised when a Cashfree API operation fails."""


def _get_base_url() -> str:
    environment = settings.CASHFREE_ENV.lower()

    if environment == "sandbox":
        return CASHFREE_SANDBOX_URL

    if environment == "production":
        return CASHFREE_PRODUCTION_URL

    raise CashfreeError(
        f"Unsupported Cashfree environment: {environment}"
    )


def _get_headers(
    idempotency_key: str | None = None,
) -> dict[str, str]:
    client_id = settings.CASHFREE_CLIENT_ID
    client_secret = settings.CASHFREE_CLIENT_SECRET

    if not client_id or not client_secret:
        raise CashfreeError(
            "Cashfree credentials are not configured."
        )

    headers = {
        "Content-Type": "application/json",
        "x-client-id": client_id,
        "x-client-secret": client_secret,
        "x-api-version": settings.CASHFREE_API_VERSION,
    }

    if idempotency_key:
        headers["x-idempotency-key"] = idempotency_key

    return headers


def _amount_to_rupees(amount_minor: int) -> float:
    if amount_minor < 100:
        raise CashfreeError(
            "Cashfree requires a minimum payment of INR 1."
        )

    return float(Decimal(amount_minor) / Decimal(100))


def _amount_to_minor(amount: Any) -> int:
    try:
        value = Decimal(str(amount))

        if not value.is_finite() or value < 0:
            raise ValueError("Amount must be finite and non-negative.")

        minor_units = value * Decimal(100)

        if minor_units != minor_units.to_integral_value():
            raise ValueError("Amount has more than two decimal places.")

        return int(minor_units)

    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CashfreeError(
            "Cashfree returned an invalid payment amount."
        ) from exc


def _customer_id(payment_id: str) -> str:
    # Cashfree customer IDs must be stable and alphanumeric.
    return "lc" + payment_id.replace("-", "")


def _normalize_phone(phone: str) -> str:
    normalized = phone.strip().replace(" ", "").replace("-", "")

    if normalized.startswith("+"):
        normalized = normalized[1:]

    if not normalized.isdigit():
        raise CashfreeError(
            "Customer phone must contain only digits and an optional leading +."
        )

    return normalized


def create_cashfree_order(
    payment_id: str,
    amount_minor: int,
    currency: str,
    customer_phone: str | None,
) -> dict[str, Any]:
    if currency.upper() != "INR":
        raise CashfreeError(
            "Cashfree integration currently supports INR only."
        )

    if not customer_phone:
        raise CashfreeError(
            "Customer phone is required for Cashfree payments."
        )

    order_id = f"lc_{payment_id.replace('-', '')}"

    payload = {
        "order_id": order_id,
        "order_amount": _amount_to_rupees(amount_minor),
        "order_currency": currency.upper(),
        "customer_details": {
            "customer_id": _customer_id(payment_id),
            "customer_phone": _normalize_phone(customer_phone),
        },
    }

    try:
        response = httpx.post(
            f"{_get_base_url()}/orders",
            json=payload,
            headers=_get_headers(payment_id),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()

    except httpx.HTTPStatusError as exc:
        raise CashfreeError(
            f"Cashfree order creation failed: {exc.response.text}"
        ) from exc

    except httpx.RequestError as exc:
        raise CashfreeError(
            f"Could not connect to Cashfree: {exc}"
        ) from exc

    except ValueError as exc:
        raise CashfreeError(
            "Cashfree returned an invalid JSON response."
        ) from exc

    if not isinstance(result, dict):
        raise CashfreeError(
            "Cashfree returned an unexpected order response."
        )

    returned_order_id = result.get("order_id")
    payment_session_id = result.get("payment_session_id")

    if returned_order_id != order_id:
        raise CashfreeError(
            "Cashfree returned an unexpected order ID."
        )

    if not payment_session_id:
        raise CashfreeError(
            "Cashfree did not return a payment session ID."
        )

    return result


def fetch_cashfree_payments(
    provider_order_id: str,
) -> list[dict[str, Any]]:
    encoded_order_id = quote(provider_order_id, safe="")

    try:
        response = httpx.get(
            f"{_get_base_url()}/orders/{encoded_order_id}/payments",
            headers=_get_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()

    except httpx.HTTPStatusError as exc:
        raise CashfreeError(
            f"Cashfree payment lookup failed: {exc.response.text}"
        ) from exc

    except httpx.RequestError as exc:
        raise CashfreeError(
            f"Could not connect to Cashfree: {exc}"
        ) from exc

    except ValueError as exc:
        raise CashfreeError(
            "Cashfree returned an invalid payment response."
        ) from exc

    if not isinstance(result, list):
        raise CashfreeError(
            "Cashfree returned an unexpected payment response."
        )

    if not all(isinstance(payment, dict) for payment in result):
        raise CashfreeError(
            "Cashfree returned an invalid payment attempt."
        )

    return result


class CashfreeAdapter(PaymentGatewayAdapter):
    """Cashfree implementation of the LedgerCore gateway interface."""

    def create_order(
        self,
        payment_id: str,
        amount_minor: int,
        currency: str,
        customer_phone: str | None = None,
    ) -> GatewayOrder:
        result = create_cashfree_order(
            payment_id=payment_id,
            amount_minor=amount_minor,
            currency=currency,
            customer_phone=customer_phone,
        )

        return GatewayOrder(
            provider="cashfree",
            provider_order_id=result["order_id"],
            amount_minor=amount_minor,
            currency=currency.upper(),
            provider_session_id=result["payment_session_id"],
            checkout_data={
                "environment": settings.CASHFREE_ENV.lower(),
            },
        )

    def fetch_payment(
        self,
        provider_order_id: str,
    ) -> GatewayPayment:
        payments = fetch_cashfree_payments(provider_order_id)

        matching_payments = [
            payment
            for payment in payments
            if payment.get("order_id") == provider_order_id
        ]

        if not matching_payments:
            raise CashfreeError(
                "No payment attempts found for this Cashfree order."
            )

        successful_payments = [
            payment
            for payment in matching_payments
            if str(payment.get("payment_status", "")).upper() == "SUCCESS"
        ]

        selected = (
            successful_payments[-1]
            if successful_payments
            else matching_payments[-1]
        )

        payment_id = selected.get("cf_payment_id")

        if not payment_id:
            raise CashfreeError(
                "Cashfree payment response is missing cf_payment_id."
            )

        payment_status = str(
            selected.get("payment_status", "")
        ).upper()

        payment_amount = selected.get("payment_amount")
        payment_currency = selected.get("payment_currency")

        if payment_amount is None:
            raise CashfreeError(
                "Cashfree payment response is missing payment_amount."
            )

        if not payment_currency:
            raise CashfreeError(
                "Cashfree payment response is missing payment_currency."
            )

        return GatewayPayment(
            provider="cashfree",
            provider_payment_id=str(payment_id),
            provider_order_id=provider_order_id,
            amount_minor=_amount_to_minor(payment_amount),
            currency=str(payment_currency).upper(),
            status=(
                "success"
                if payment_status == "SUCCESS"
                else payment_status.lower()
            ),
            captured=(
                payment_status == "SUCCESS"
                and selected.get("is_captured") is True
            ),
        )
