
import hashlib
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


class PayUAdapter(GatewayAdapter):
    provider_name = "payu"
    simulated = False

    def __init__(self) -> None:
        settings = get_settings()

        self.merchant_key = settings.payu_merchant_key
        self.merchant_salt = settings.payu_merchant_salt
        self.environment = settings.payu_environment
        self.success_url = settings.payu_success_url
        self.failure_url = settings.payu_failure_url
        self.timeout_seconds = settings.payu_timeout_seconds

        if self.environment == "production":
            self.payment_url = "https://secure.payu.in/_payment"
            self.verify_url = (
                "https://info.payu.in/merchant/postservice.php?form=2"
            )
        else:
            self.payment_url = "https://test.payu.in/_payment"
            self.verify_url = (
                "https://test.payu.in/merchant/postservice.php?form=2"
            )

    def _validate_configuration(self) -> None:
        if not self.merchant_key or not self.merchant_salt:
            raise GatewayConfigurationError(
                "PayU merchant key or salt is not configured"
            )

        if self.environment not in {"sandbox", "production"}:
            raise GatewayConfigurationError(
                "PAYU_ENV must be either 'sandbox' or 'production'"
            )

        if not self.success_url or not self.failure_url:
            raise GatewayConfigurationError(
                "PAYU_SUCCESS_URL and PAYU_FAILURE_URL must be configured"
            )

    @staticmethod
    def _transaction_id(payment: RouterPaymentRequest) -> str:
        # PayU transaction IDs are kept deterministic so that reconciliation
        # always checks the same transaction after an uncertain outcome.
        return payment.payment_id.hex[:25]

    @staticmethod
    def _format_amount(amount_minor: int) -> str:
        amount = Decimal(amount_minor) / Decimal("100")
        return f"{amount:.2f}"

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha512(value.encode("utf-8")).hexdigest()

    def _checkout_hash(
        self,
        *,
        txnid: str,
        amount: str,
        productinfo: str,
        firstname: str,
        email: str,
        udf1: str,
    ) -> str:
        # PayU hosted checkout hash includes UDF1 through UDF10.
        # Empty UDF values must remain in the hash sequence.
        hash_values = [
            self.merchant_key,
            txnid,
            amount,
            productinfo,
            firstname,
            email,
            udf1,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            self.merchant_salt,
        ]

        return self._hash("|".join(hash_values))

    def _verification_hash(self, txnid: str) -> str:
        command = "verify_payment"
        return self._hash(
            f"{self.merchant_key}|{command}|{txnid}|{self.merchant_salt}"
        )

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise GatewayAdapterError(
                "PayU returned an invalid JSON response"
            ) from exc

        if not isinstance(data, dict):
            raise GatewayAdapterError(
                "PayU returned an unexpected response structure"
            )

        return data

    def _build_result(
        self,
        payment: RouterPaymentRequest,
        txnid: str,
        transaction: dict,
    ) -> RouterPaymentResult:
        returned_txnid = str(transaction.get("txnid", txnid))

        if returned_txnid != txnid:
            raise GatewayAdapterError(
                "PayU returned a transaction ID that does not match"
            )

        try:
            returned_amount = Decimal(str(transaction["amount"]))
            expected_amount = Decimal(
                self._format_amount(payment.amount_minor)
            )
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise GatewayAdapterError(
                "PayU returned an invalid transaction amount"
            ) from exc

        if returned_amount != expected_amount:
            raise GatewayAdapterError(
                "PayU transaction amount does not match the payment"
            )

        status = str(transaction.get("status", "unknown")).lower()

        mihpayid = transaction.get("mihpayid")
        session_id = (
            str(mihpayid)
            if mihpayid and str(mihpayid).lower() != "not found"
            else txnid
        )

        return RouterPaymentResult(
            payment_id=payment.payment_id,
            provider=self.provider_name,
            provider_order_id=txnid,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            status=status,
            simulated=False,
            provider_session_id=session_id,
            checkout_data={
                "txnid": txnid,
                "mihpayid": mihpayid,
                "status": status,
            },
        )

    async def create_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult:
        """
        Prepare PayU hosted checkout fields.

        PayU's hosted checkout is initiated by submitting the signed form
        from the frontend. This method does not make a payment request
        or claim that a PayU transaction has already been created.
        """
        self._validate_configuration()

        if payment.currency != "INR":
            raise GatewayRejectedError(
                "This PayU integration currently supports INR payments only"
            )

        if not payment.customer_name or not payment.customer_email:
            raise GatewayRejectedError(
                "PayU checkout requires customer name and email"
            )

        if not payment.customer_phone:
            raise GatewayRejectedError(
                "PayU checkout requires a customer phone number"
            )

        txnid = self._transaction_id(payment)
        amount = self._format_amount(payment.amount_minor)
        productinfo = f"LedgerCore payment {payment.payment_id}"
        firstname = payment.customer_name[:60]
        email = payment.customer_email[:50]
        udf1 = str(payment.payment_id)

        fields = {
            "key": self.merchant_key,
            "txnid": txnid,
            "amount": amount,
            "productinfo": productinfo,
            "firstname": firstname,
            "email": email,
            "phone": payment.customer_phone,
            "surl": self.success_url,
            "furl": self.failure_url,
            "udf1": udf1,
        }

        fields["hash"] = self._checkout_hash(
            txnid=txnid,
            amount=amount,
            productinfo=productinfo,
            firstname=firstname,
            email=email,
            udf1=udf1,
        )

        return RouterPaymentResult(
            payment_id=payment.payment_id,
            provider=self.provider_name,
            provider_order_id=txnid,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            status="checkout_pending",
            simulated=False,
            provider_session_id=txnid,
            checkout_data={
                "payment_url": self.payment_url,
                "method": "POST",
                "fields": fields,
            },
        )

    async def reconcile_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult | None:
        """
        Verify an existing PayU transaction.

        Returns None only when PayU explicitly reports that the
        deterministic transaction ID was not found.
        This method never initiates a new checkout.
        """
        self._validate_configuration()

        txnid = self._transaction_id(payment)

        payload = {
            "key": self.merchant_key,
            "command": "verify_payment",
            "var1": txnid,
            "hash": self._verification_hash(txnid),
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.post(
                    self.verify_url,
                    data=payload,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                )

        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError(
                "PayU transaction verification timed out"
            ) from exc

        except httpx.RequestError as exc:
            raise GatewayUnavailableError(
                "PayU verification network error"
            ) from exc

        if response.status_code in (401, 403):
            raise GatewayConfigurationError(
                "PayU authentication failed during verification"
            )

        if response.status_code >= 500:
            raise GatewayUnavailableError(
                "PayU verification service is temporarily unavailable"
            )

        if response.status_code != 200:
            raise GatewayAdapterError(
                "Unexpected PayU verification response: "
                f"HTTP {response.status_code}"
            )

        data = self._parse_response(response)
        transaction_details = data.get("transaction_details")

        if not isinstance(transaction_details, dict):
            raise GatewayAdapterError(
                "PayU returned invalid transaction verification data"
            )

        transaction = transaction_details.get(txnid)

        if not isinstance(transaction, dict):
            raise GatewayAdapterError(
                "PayU verification response did not contain the requested "
                "transaction"
            )

        transaction_status = str(
            transaction.get("status", "")
        ).strip().lower()

        mihpayid = str(transaction.get("mihpayid", "")).strip().lower()

        if (
            transaction_status == "not found"
            and mihpayid == "not found"
        ):
            return None

        return self._build_result(
            payment,
            txnid,
            transaction,
        )
