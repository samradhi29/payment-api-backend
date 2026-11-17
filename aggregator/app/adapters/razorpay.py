
import logging
from typing import Any

import httpx

from app.adapters.base import (
    GatewayAdapter,
    GatewayAdapterError,
    GatewayConfigurationError,
    GatewayRejectedError,
    GatewayTimeoutError,
    GatewayUnavailableError,
)
from app.config import get_settings
from app.models import RouterPaymentRequest, RouterPaymentResult


logger = logging.getLogger(__name__)


class RazorpayAdapter(GatewayAdapter):
    provider_name = "razorpay"
    simulated = False

    def __init__(self) -> None:
        settings = get_settings()
        self.key_id = settings.razorpay_key_id
        self.key_secret = settings.razorpay_key_secret
        self.base_url = settings.razorpay_base_url.rstrip("/")
        self.timeout_seconds = settings.razorpay_timeout_seconds

    def _validate_configuration(self) -> None:
        if not self.key_id or not self.key_secret:
            raise GatewayConfigurationError(
                "Razorpay credentials are not configured"
            )

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise GatewayAdapterError(
                "Razorpay returned an invalid JSON response"
            ) from exc

        if not isinstance(data, dict):
            raise GatewayAdapterError(
                "Razorpay returned an unexpected response structure"
            )

        return data

    @staticmethod
    def _error_description(data: dict[str, Any]) -> str:
        error = data.get("error", {})
        if isinstance(error, dict):
            return str(
                error.get("description")
                or error.get("reason")
                or error.get("code")
                or "Unknown Razorpay error"
            )
        return "Unknown Razorpay error"

    def _build_result(
        self,
        payment: RouterPaymentRequest,
        order: dict[str, Any],
    ) -> RouterPaymentResult:
        order_id = order.get("id")

        if not order_id:
            raise GatewayAdapterError(
                "Razorpay order response did not contain an order ID"
            )

        try:
            amount = int(order["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GatewayAdapterError(
                "Razorpay returned an invalid order amount"
            ) from exc

        currency = str(order.get("currency", "")).upper()

        if amount != payment.amount_minor:
            raise GatewayAdapterError(
                "Razorpay order amount does not match the payment"
            )

        if currency != payment.currency:
            raise GatewayAdapterError(
                "Razorpay order currency does not match the payment"
            )

        return RouterPaymentResult(
            payment_id=payment.payment_id,
            provider=self.provider_name,
            provider_order_id=str(order_id),
            amount_minor=amount,
            currency=currency,
            status=str(order.get("status", "created")).lower(),
            simulated=False,
            provider_session_id=str(order_id),
            checkout_data={
                "key_id": self.key_id,
                "order_id": str(order_id),
                "amount": amount,
                "currency": currency,
                "name": "LedgerCore",
                "description": f"Payment {payment.payment_id}",
                "prefill": {
                    "name": payment.customer_name or "",
                    "email": payment.customer_email or "",
                    "contact": payment.customer_phone or "",
                },
            },
        )

    async def create_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult:
        self._validate_configuration()

        receipt = str(payment.payment_id)

        payload = {
            "amount": payment.amount_minor,
            "currency": payment.currency,
            "receipt": receipt,
            "notes": {
                "ledgercore_payment_id": str(payment.payment_id),
                "ledgercore_idempotency_key": payment.idempotency_key,
            },
        }

        url = f"{self.base_url}/orders"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                auth=(self.key_id, self.key_secret),
            ) as client:
                response = await client.post(url, json=payload)

        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError(
                "Razorpay order creation timed out; payment outcome is uncertain"
            ) from exc

        except httpx.RequestError as exc:
            raise GatewayUnavailableError(
                "Razorpay network error; order creation outcome is uncertain"
            ) from exc

        if response.status_code in (401, 403):
            raise GatewayConfigurationError(
                "Razorpay authentication failed; check API credentials"
            )

        if response.status_code >= 500:
            raise GatewayUnavailableError(
                f"Razorpay server error: HTTP {response.status_code}"
            )

        data = self._parse_response(response)

        if response.status_code >= 400:
            description = self._error_description(data)

            # A duplicate receipt may mean an earlier request created
            # the order. It must be reconciled, not treated as safe rejection.
            if "receipt" in description.lower() and (
                "duplicate" in description.lower()
                or "already" in description.lower()
                or "exist" in description.lower()
            ):
                raise GatewayAdapterError(
                    "Razorpay reported a duplicate receipt; "
                    "the existing order must be reconciled"
                )

            raise GatewayRejectedError(
                f"Razorpay rejected order creation: {description}"
            )

        return self._build_result(payment, data)

    async def reconcile_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult | None:
        """
        Find an existing Razorpay order using its LedgerCore receipt.

        Returns None only after every matching order page has been checked.
        This method never creates an order.
        """
        self._validate_configuration()

        receipt = str(payment.payment_id)
        url = f"{self.base_url}/orders"

        skip = 0
        page_size = 100
        matching_orders: list[dict[str, Any]] = []

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                auth=(self.key_id, self.key_secret),
            ) as client:
                while True:
                    response = await client.get(
                        url,
                        params={
                            "receipt": receipt,
                            "count": page_size,
                            "skip": skip,
                        },
                    )

                    if response.status_code in (401, 403):
                        raise GatewayConfigurationError(
                            "Razorpay authentication failed during reconciliation"
                        )

                    if response.status_code >= 500:
                        raise GatewayUnavailableError(
                            "Razorpay reconciliation is temporarily unavailable"
                        )

                    if response.status_code != 200:
                        raise GatewayAdapterError(
                            "Unexpected Razorpay reconciliation response: "
                            f"HTTP {response.status_code}"
                        )

                    data = self._parse_response(response)
                    items = data.get("items")

                    if not isinstance(items, list):
                        raise GatewayAdapterError(
                            "Razorpay returned invalid reconciliation data"
                        )

                    for order in items:
                        if not isinstance(order, dict):
                            continue

                        if str(order.get("receipt", "")) == receipt:
                            matching_orders.append(order)

                    if len(items) < page_size:
                        break

                    skip += len(items)

                    # Do not claim an order is absent if pagination cannot
                    # be completed safely.
                    if skip > 10000:
                        raise GatewayUnavailableError(
                            "Razorpay reconciliation exceeded the safe "
                            "pagination limit"
                        )

        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError(
                "Razorpay reconciliation timed out"
            ) from exc

        except httpx.RequestError as exc:
            raise GatewayUnavailableError(
                "Razorpay reconciliation network error"
            ) from exc

        if not matching_orders:
            return None

        if len(matching_orders) > 1:
            raise GatewayAdapterError(
                "Multiple Razorpay orders matched the same LedgerCore payment"
            )

        return self._build_result(payment, matching_orders[0])
