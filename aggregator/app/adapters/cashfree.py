
import logging
from decimal import Decimal, InvalidOperation

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


class CashfreeAdapter(GatewayAdapter):
    provider_name = "cashfree"
    simulated = False

    def __init__(self) -> None:
        settings = get_settings()

        self.client_id = settings.cashfree_client_id
        self.client_secret = settings.cashfree_client_secret
        self.environment = settings.cashfree_environment
        self.api_version = settings.cashfree_api_version
        self.timeout_seconds = settings.cashfree_timeout_seconds

        if self.environment == "production":
            self.base_url = "https://api.cashfree.com/pg"
        else:
            self.base_url = "https://sandbox.cashfree.com/pg"

    def _validate_configuration(self) -> None:
        if not self.client_id or not self.client_secret:
            raise GatewayConfigurationError(
                "Cashfree credentials are not configured"
            )

        if self.environment not in {"sandbox", "production"}:
            raise GatewayConfigurationError(
                "CASHFREE_ENV must be either 'sandbox' or 'production'"
            )

        if not self.api_version:
            raise GatewayConfigurationError(
                "Cashfree API version is not configured"
            )

    def _headers(self) -> dict[str, str]:
        return {
            "x-client-id": self.client_id,
            "x-client-secret": self.client_secret,
            "x-api-version": self.api_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise GatewayAdapterError(
                "Cashfree returned an invalid JSON response"
            ) from exc

        if not isinstance(data, dict):
            raise GatewayAdapterError(
                "Cashfree returned an unexpected response structure"
            )

        return data

    @staticmethod
    def _amount_to_major(amount_minor: int) -> float:
        return float(Decimal(amount_minor) / Decimal("100"))

    @staticmethod
    def _amount_to_minor(value: object) -> int:
        try:
            amount = Decimal(str(value))
            minor_amount = amount * Decimal("100")
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise GatewayAdapterError(
                "Cashfree returned an invalid order amount"
            ) from exc

        if minor_amount != minor_amount.to_integral_value():
            raise GatewayAdapterError(
                "Cashfree returned an amount with unsupported precision"
            )

        return int(minor_amount)

    @staticmethod
    def _error_message(data: dict) -> str:
        return str(
            data.get("message")
            or data.get("type")
            or data.get("code")
            or "Unknown Cashfree error"
        )

    def _build_result(
        self,
        payment: RouterPaymentRequest,
        data: dict,
    ) -> RouterPaymentResult:
        order_id = data.get("order_id")
        session_id = data.get("payment_session_id")

        if not order_id:
            raise GatewayAdapterError(
                "Cashfree response did not contain an order ID"
            )

        if str(order_id) != f"lc_{payment.payment_id.hex}":
            raise GatewayAdapterError(
                "Cashfree returned an unexpected order ID"
            )

        amount_minor = self._amount_to_minor(data.get("order_amount"))

        currency = str(data.get("order_currency", "")).upper()

        if amount_minor != payment.amount_minor:
            raise GatewayAdapterError(
                "Cashfree order amount does not match the payment"
            )

        if currency != payment.currency:
            raise GatewayAdapterError(
                "Cashfree order currency does not match the payment"
            )

        if not session_id:
            raise GatewayAdapterError(
                "Cashfree response did not contain a payment session ID"
            )

        return RouterPaymentResult(
            payment_id=payment.payment_id,
            provider=self.provider_name,
            provider_order_id=str(order_id),
            amount_minor=amount_minor,
            currency=currency,
            status=str(data.get("order_status", "ACTIVE")).lower(),
            simulated=False,
            provider_session_id=str(session_id),
            checkout_data={
                "payment_session_id": str(session_id),
                "order_id": str(order_id),
                "environment": self.environment,
            },
        )

    async def create_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult:
        self._validate_configuration()

        order_id = f"lc_{payment.payment_id.hex}"

        customer_details = {
            "customer_id": f"lc_customer_{payment.payment_id.hex}",
            "customer_phone": payment.customer_phone or "9999999999",
        }

        if payment.customer_email:
            customer_details["customer_email"] = payment.customer_email

        if payment.customer_name:
            customer_details["customer_name"] = payment.customer_name

        payload = {
            "order_id": order_id,
            "order_amount": self._amount_to_major(payment.amount_minor),
            "order_currency": payment.currency,
            "customer_details": customer_details,
            "order_note": f"LedgerCore payment {payment.payment_id}",
            "order_tags": {
                "ledgercore_payment_id": str(payment.payment_id),
            },
        }

        url = f"{self.base_url}/orders"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                )

        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError(
                "Cashfree order creation timed out; "
                "the order may already exist"
            ) from exc

        except httpx.RequestError as exc:
            raise GatewayUnavailableError(
                "Cashfree network error; "
                "the order creation outcome is uncertain"
            ) from exc

        if response.status_code in (401, 403):
            raise GatewayConfigurationError(
                "Cashfree authentication failed; check API credentials"
            )

        if response.status_code >= 500:
            raise GatewayUnavailableError(
                f"Cashfree server error: HTTP {response.status_code}"
            )

        if response.status_code in (408, 409, 425, 429):
            raise GatewayUnavailableError(
                "Cashfree returned an uncertain order creation response: "
                f"HTTP {response.status_code}"
            )

        data = self._parse_response(response)

        if response.status_code >= 400:
            raise GatewayRejectedError(
                f"Cashfree rejected order creation: "
                f"{self._error_message(data)}"
            )

        return self._build_result(payment, data)

    async def reconcile_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult | None:
        """
        Retrieve the deterministic Cashfree order ID.

        None is returned only for an explicit HTTP 404 response.
        This method never creates an order.
        """
        self._validate_configuration()

        order_id = f"lc_{payment.payment_id.hex}"
        url = f"{self.base_url}/orders/{order_id}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(
                    url,
                    headers=self._headers(),
                )

        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError(
                "Cashfree reconciliation timed out"
            ) from exc

        except httpx.RequestError as exc:
            raise GatewayUnavailableError(
                "Cashfree reconciliation network error"
            ) from exc

        if response.status_code == 404:
            return None

        if response.status_code in (401, 403):
            raise GatewayConfigurationError(
                "Cashfree authentication failed during reconciliation"
            )

        if response.status_code >= 500:
            raise GatewayUnavailableError(
                "Cashfree reconciliation is temporarily unavailable"
            )

        if response.status_code != 200:
            raise GatewayAdapterError(
                "Unexpected Cashfree reconciliation response: "
                f"HTTP {response.status_code}"
            )

        data = self._parse_response(response)
        return self._build_result(payment, data)
