
import hashlib
import hmac
import logging
from typing import Any
from uuid import UUID

import httpx

from app.core.config import settings
from app.providers.base import (
    GatewayOrder,
    GatewayPayment,
    GatewayError,
    PaymentGatewayAdapter,
)


logger = logging.getLogger(__name__)

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"
RAZORPAY_PAYMENTS_URL = "https://api.razorpay.com/v1/payments"
REQUEST_TIMEOUT = 15.0


class RazorpayError(GatewayError):
    """Raised when Razorpay API communication fails."""


def _validate_credentials() -> None:
    if not settings.RAZORPAY_KEY_ID:
        raise RazorpayError("Razorpay Key ID is not configured")

    if not settings.RAZORPAY_KEY_SECRET:
        raise RazorpayError("Razorpay Key Secret is not configured")


def create_razorpay_order(
    payment_id: UUID,
    amount_minor: int,
    currency: str = "INR",
) -> dict[str, Any]:

    _validate_credentials()

    if amount_minor <= 0:
        raise ValueError("Payment amount must be greater than zero")

    if not currency or len(currency) != 3:
        raise ValueError("Invalid currency")

    payload = {
        "amount": amount_minor,
        "currency": currency.upper(),
        "receipt": f"lc_{payment_id}",
        "notes": {"payment_id": str(payment_id)},
    }

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post(
                RAZORPAY_ORDERS_URL,
                json=payload,
                auth=(
                    settings.RAZORPAY_KEY_ID,
                    settings.RAZORPAY_KEY_SECRET,
                ),
            )

            response.raise_for_status()
            order = response.json()

            logger.info(
                "Razorpay order created | Payment ID: %s | Order ID: %s",
                payment_id,
                order.get("id"),
            )

            return order

    except httpx.TimeoutException as exc:
        raise RazorpayError("Razorpay request timed out") from exc

    except httpx.HTTPStatusError as exc:
        raise RazorpayError(
            f"Razorpay rejected the request "
            f"(HTTP {exc.response.status_code})"
        ) from exc

    except httpx.RequestError as exc:
        raise RazorpayError("Could not connect to Razorpay") from exc

    except (ValueError, TypeError) as exc:
        raise RazorpayError(
            "Invalid response received from Razorpay"
        ) from exc


def verify_razorpay_signature(
    order_id: str,
    payment_id: str,
    razorpay_signature: str,
) -> bool:
    """Verify Razorpay Checkout signature using HMAC-SHA256."""

    _validate_credentials()

    if not order_id or not payment_id or not razorpay_signature:
        return False

    message = f"{order_id}|{payment_id}"

    expected_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        razorpay_signature,
    )


def fetch_razorpay_payment(payment_id: str) -> dict[str, Any]:
    """Fetch an individual payment directly from Razorpay."""

    _validate_credentials()

    if not payment_id:
        raise ValueError("Razorpay Payment ID is required")

    url = f"{RAZORPAY_PAYMENTS_URL}/{payment_id}"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(
                url,
                auth=(
                    settings.RAZORPAY_KEY_ID,
                    settings.RAZORPAY_KEY_SECRET,
                ),
            )

            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException as exc:
        raise RazorpayError(
            "Razorpay payment lookup timed out"
        ) from exc

    except httpx.HTTPStatusError as exc:
        raise RazorpayError(
            f"Razorpay payment lookup failed "
            f"(HTTP {exc.response.status_code})"
        ) from exc

    except httpx.RequestError as exc:
        raise RazorpayError(
            "Could not connect to Razorpay"
        ) from exc

    except (ValueError, TypeError) as exc:
        raise RazorpayError(
            "Invalid payment response received from Razorpay"
        ) from exc


class RazorpayAdapter(PaymentGatewayAdapter):
    """Razorpay implementation of the common gateway interface."""

    def create_order(
        self,
        payment_id: str,
        amount_minor: int,
        currency: str,
        customer_phone: str | None = None,
    ) -> GatewayOrder:

        order = create_razorpay_order(
            payment_id=UUID(payment_id),
            amount_minor=amount_minor,
            currency=currency,
        )

        provider_order_id = order.get("id")

        if not provider_order_id:
            raise RazorpayError(
                "Razorpay response did not contain an order ID"
            )

        return GatewayOrder(
            provider="razorpay",
            provider_order_id=provider_order_id,
            amount_minor=amount_minor,
            currency=currency.upper(),
            checkout_data={
                "key_id": settings.RAZORPAY_KEY_ID,
            },
        )

    def fetch_payment(
        self,
        provider_order_id: str,
    ) -> GatewayPayment:

        _validate_credentials()

        if not provider_order_id:
            raise ValueError("Razorpay Order ID is required")

        url = (
            f"{RAZORPAY_ORDERS_URL}/"
            f"{provider_order_id}/payments"
        )

        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                response = client.get(
                    url,
                    auth=(
                        settings.RAZORPAY_KEY_ID,
                        settings.RAZORPAY_KEY_SECRET,
                    ),
                )

                response.raise_for_status()
                data = response.json()

        except httpx.TimeoutException as exc:
            raise RazorpayError(
                "Razorpay payment lookup timed out"
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise RazorpayError(
                f"Razorpay payment lookup failed "
                f"(HTTP {exc.response.status_code})"
            ) from exc

        except httpx.RequestError as exc:
            raise RazorpayError(
                "Could not connect to Razorpay"
            ) from exc

        except (ValueError, TypeError) as exc:
            raise RazorpayError(
                "Invalid payment response received from Razorpay"
            ) from exc

        payments = data.get("items", [])

        if not payments:
            raise RazorpayError(
                "No payments found for this Razorpay order"
            )

        # Prefer a captured payment. Otherwise return the latest
        # attempt so the caller can handle its actual status.
        captured_payments = [
            payment
            for payment in payments
            if payment.get("captured") is True
        ]

        selected = (
            captured_payments[-1]
            if captured_payments
            else payments[-1]
        )

        return GatewayPayment(
            provider="razorpay",
            provider_payment_id=str(selected.get("id", "")),
            provider_order_id=str(selected.get("order_id", "")),
            amount_minor=int(selected.get("amount", 0)),
            currency=str(selected.get("currency", "")).upper(),
            status=str(selected.get("status", "")).lower(),
            captured=selected.get("captured") is True,
        )
