
import hashlib
import logging
import os
from typing import Any
from uuid import UUID

import httpx


logger = logging.getLogger(__name__)

AGGREGATOR_BASE_URL = os.getenv(
    "AGGREGATOR_BASE_URL",
    "http://ledgercore-aggregator-api:8100",
).rstrip("/")

AGGREGATOR_TIMEOUT_SECONDS = float(
    os.getenv("AGGREGATOR_TIMEOUT_SECONDS", "12")
)


class AggregatorError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        outcome_unknown: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown


def build_routing_idempotency_key(
    merchant_id: UUID,
    idempotency_key: str,
) -> str:
    raw = f"{merchant_id}:{idempotency_key}"

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def create_aggregated_gateway_order(
    *,
    payment_id: UUID,
    merchant_id: UUID,
    amount_minor: int,
    currency: str,
    idempotency_key: str,
    allowed_providers: list[str],
    customer_phone: str | None = None,
    customer_email: str | None = None,
    customer_name: str | None = None,
    payment_method: str | None = None,
    bank_code: str | None = None,
    preferred_provider: str | None = None,
) -> dict[str, Any]:
    if not allowed_providers:
        raise AggregatorError(
            "No eligible payment providers were supplied"
        )

    request_body = {
        "payment_id": str(payment_id),
        "amount_minor": amount_minor,
        "currency": currency.upper(),
        "idempotency_key": build_routing_idempotency_key(
            merchant_id,
            idempotency_key,
        ),
        "payment_method": payment_method,
        "bank_code": bank_code,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
        "customer_name": customer_name,
    }

    params = [
        ("allowed_providers", provider)
        for provider in allowed_providers
    ]

    if preferred_provider:
        params.append(
            ("preferred_provider", preferred_provider.strip().lower())
        )

    try:
        with httpx.Client(
            timeout=AGGREGATOR_TIMEOUT_SECONDS,
        ) as client:
            response = client.post(
                f"{AGGREGATOR_BASE_URL}/v1/router/route",
                json=request_body,
                params=params,
            )

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        logger.exception(
            "Aggregator request outcome is unknown for payment %s",
            payment_id,
        )

        raise AggregatorError(
            "Aggregator request timed out or connection was interrupted",
            outcome_unknown=True,
        ) from exc

    except httpx.RequestError as exc:
        logger.exception(
            "Could not communicate with aggregator for payment %s",
            payment_id,
        )

        raise AggregatorError(
            "Could not communicate with payment aggregator",
            outcome_unknown=True,
        ) from exc

    try:
        response_data = response.json()
    except ValueError as exc:
        raise AggregatorError(
            "Aggregator returned an invalid response",
            status_code=response.status_code,
            outcome_unknown=response.status_code >= 500,
        ) from exc

    if response.is_error:
        detail = (
            response_data.get("detail")
            if isinstance(response_data, dict)
            else None
        )

        if isinstance(detail, dict):
            message = detail.get(
                "message",
                "Payment aggregator request failed",
            )
            code = detail.get("code")
        else:
            message = str(
                detail or "Payment aggregator request failed"
            )
            code = None

        outcome_unknown = (
            code == "UNKNOWN_PAYMENT_OUTCOME"
            or response.status_code == 504
        )

        raise AggregatorError(
            message,
            status_code=response.status_code,
            outcome_unknown=outcome_unknown,
        )

    if not isinstance(response_data, dict):
        raise AggregatorError(
            "Aggregator returned an invalid response structure"
        )

    result = response_data.get("result")

    if not isinstance(result, dict):
        raise AggregatorError(
            "Aggregator response is missing the payment result"
        )

    required_fields = (
        "payment_id",
        "provider",
        "provider_order_id",
        "amount_minor",
        "currency",
        "status",
    )

    missing_fields = [
        field
        for field in required_fields
        if result.get(field) is None
    ]

    if missing_fields:
        raise AggregatorError(
            "Aggregator result is missing required fields: "
            + ", ".join(missing_fields)
        )

    if str(result["payment_id"]) != str(payment_id):
        raise AggregatorError(
            "Aggregator returned a result for a different payment"
        )

    if int(result["amount_minor"]) != amount_minor:
        raise AggregatorError(
            "Aggregator returned an unexpected payment amount"
        )

    if str(result["currency"]).upper() != currency.upper():
        raise AggregatorError(
            "Aggregator returned an unexpected payment currency"
        )

    selected_provider = str(result["provider"]).strip().lower()

    if selected_provider not in {
        provider.strip().lower()
        for provider in allowed_providers
    }:
        raise AggregatorError(
            "Aggregator selected a provider outside the allowed list"
        )

    if not result["provider_order_id"]:
        raise AggregatorError(
            "Aggregator returned an empty provider order ID"
        )

    return response_data
