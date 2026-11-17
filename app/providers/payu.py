
import hashlib
import hmac
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.providers.base import (
    GatewayError,
    GatewayOrder,
    GatewayPayment,
    PaymentGatewayAdapter,
)


class PayUAdapter(PaymentGatewayAdapter):
    def __init__(self) -> None:
        self.merchant_key = os.getenv("PAYU_MERCHANT_KEY", "").strip()
        self.merchant_salt = os.getenv("PAYU_MERCHANT_SALT", "").strip()
        self.environment = os.getenv("PAYU_ENV", "sandbox").strip().lower()

        if not self.merchant_key or not self.merchant_salt:
            raise GatewayError("PayU merchant credentials are not configured.")

        if self.environment not in ("sandbox", "production"):
            raise GatewayError("Invalid PAYU_ENV configuration.")

        if self.environment == "production":
            self.checkout_url = "https://secure.payu.in/_payment"
            self.verify_url = (
                "https://info.payu.in/merchant/postservice.php?form=2"
            )
        else:
            self.checkout_url = "https://test.payu.in/_payment"
            self.verify_url = (
                "https://test.payu.in/merchant/postservice.php?form=2"
            )

        self.success_url = os.getenv("PAYU_SUCCESS_URL", "").strip()
        self.failure_url = os.getenv("PAYU_FAILURE_URL", "").strip()

    @staticmethod
    def _sha512(value: str) -> str:
        return hashlib.sha512(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _format_amount(amount_minor: int) -> str:
        return f"{Decimal(amount_minor) / Decimal(100):.2f}"

    def _request_hash(
        self,
        txnid: str,
        amount: str,
        productinfo: str,
        firstname: str,
        email: str,
        udf1: str = "",
        udf2: str = "",
        udf3: str = "",
        udf4: str = "",
        udf5: str = "",
    ) -> str:
        hash_string = "|".join(
            [
                self.merchant_key,
                txnid,
                amount,
                productinfo,
                firstname,
                email,
                udf1,
                udf2,
                udf3,
                udf4,
                udf5,
                "",
                "",
                "",
                "",
                "",
                self.merchant_salt,
            ]
        )

        return self._sha512(hash_string)

    def verify_response_hash(
        self,
        data: dict[str, str],
    ) -> bool:
        required_fields = (
            "status",
            "txnid",
            "amount",
            "productinfo",
            "firstname",
            "email",
            "key",
            "hash",
        )

        if any(not data.get(field) for field in required_fields):
            return False

        if not hmac.compare_digest(
            str(data["key"]),
            self.merchant_key,
        ):
            return False

        hash_parts = [
            self.merchant_salt,
            data["status"],
        ]

        if data.get("splitInfo"):
            hash_parts.append(data["splitInfo"])
            hash_parts.extend([""] * 5)
        else:
            hash_parts.extend([""] * 5)

        hash_parts.extend(
            [
                data.get("udf5", ""),
                data.get("udf4", ""),
                data.get("udf3", ""),
                data.get("udf2", ""),
                data.get("udf1", ""),
                data["email"],
                data["firstname"],
                data["productinfo"],
                data["amount"],
                data["txnid"],
                data["key"],
            ]
        )

        hash_string = "|".join(hash_parts)

        if data.get("additional_charges"):
            hash_string = (
                f"{data['additional_charges']}|{hash_string}"
            )

        calculated_hash = self._sha512(hash_string)

        return hmac.compare_digest(
            calculated_hash.lower(),
            str(data["hash"]).strip().lower(),
        )

    def create_order(
        self,
        payment_id: str,
        amount_minor: int,
        currency: str,
        customer_phone: str | None = None,
        customer_email: str | None = None,
        customer_name: str | None = None,
        **kwargs: Any,
    ) -> GatewayOrder:
        if currency.upper() != "INR":
            raise GatewayError("PayU supports INR payments only.")

        if amount_minor <= 0:
            raise GatewayError("Payment amount must be positive.")

        if not customer_phone:
            raise GatewayError("Customer phone is required for PayU.")

        if not customer_email:
            raise GatewayError("Customer email is required for PayU.")

        if not self.success_url or not self.failure_url:
            raise GatewayError(
                "PAYU_SUCCESS_URL and PAYU_FAILURE_URL must be configured."
            )

        txnid = payment_id.replace("-", "")[:25]
        amount = self._format_amount(amount_minor)
        productinfo = "LedgerCore Payment"
        firstname = (customer_name or "Customer").strip().split()[0]

        fields = {
            "key": self.merchant_key,
            "txnid": txnid,
            "amount": amount,
            "productinfo": productinfo,
            "firstname": firstname,
            "email": customer_email,
            "phone": customer_phone,
            "surl": self.success_url,
            "furl": self.failure_url,
            "service_provider": "payu_paisa",
            "udf1": payment_id,
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
        }

        fields["hash"] = self._request_hash(
            txnid=txnid,
            amount=amount,
            productinfo=productinfo,
            firstname=firstname,
            email=customer_email,
            udf1=fields["udf1"],
            udf2=fields["udf2"],
            udf3=fields["udf3"],
            udf4=fields["udf4"],
            udf5=fields["udf5"],
        )

        return GatewayOrder(
            provider="payu",
            provider_order_id=txnid,
            amount_minor=amount_minor,
            currency=currency.upper(),
            provider_session_id=None,
            checkout_data={
                "checkout_url": self.checkout_url,
                "method": "POST",
                "fields": fields,
                "environment": self.environment,
            },
        )

    def fetch_payment(
        self,
        provider_order_id: str,
    ) -> GatewayPayment:
        command = "verify_payment"

        request_hash = self._sha512(
            "|".join(
                [
                    self.merchant_key,
                    command,
                    provider_order_id,
                    self.merchant_salt,
                ]
            )
        )

        payload = {
            "key": self.merchant_key,
            "command": command,
            "var1": provider_order_id,
            "hash": request_hash,
        }

        try:
            response = httpx.post(
                self.verify_url,
                data=payload,
                timeout=20.0,
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GatewayError(
                f"PayU payment verification request failed: {exc}"
            ) from exc

        if str(result.get("status")) != "1":
            raise GatewayError(
                f"PayU verification failed: {result.get('msg', 'Unknown error')}"
            )

        transactions = result.get("transaction_details", {})
        transaction = transactions.get(provider_order_id)

        if not isinstance(transaction, dict):
            raise GatewayError(
                "PayU verification did not return transaction details."
            )

        try:
            verified_amount = (
                Decimal(str(transaction["amt"])) * Decimal(100)
            )

            if verified_amount != verified_amount.to_integral_value():
                raise GatewayError("PayU returned an invalid payment amount.")

            amount_minor = int(verified_amount)
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise GatewayError(
                "PayU returned an invalid transaction amount."
            ) from exc

        transaction_status = str(
            transaction.get("status", "")
        ).strip().lower()

        captured = transaction_status == "success"

        return GatewayPayment(
            provider="payu",
            provider_payment_id=str(
                transaction.get("mihpayid") or provider_order_id
            ),
            provider_order_id=provider_order_id,
            amount_minor=amount_minor,
            currency="INR",
            status="success" if captured else transaction_status,
            captured=captured,
        )
