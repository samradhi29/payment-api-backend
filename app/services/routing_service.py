
import hashlib
import logging
import os
from uuid import UUID

import httpx

from app.providers.registry import GATEWAY_PROVIDERS

logger = logging.getLogger(__name__)

AGGREGATOR_BASE_URL = os.getenv(
    "AGGREGATOR_BASE_URL",
    "http://localhost:8100",
).rstrip("/")

ROUTING_TIMEOUT_SECONDS = 2.5

PREFERRED_GATEWAY_SCORE_TOLERANCE = 0.000001


class RoutingUnavailableError(Exception):
    """Raised when automatic gateway selection cannot be completed."""


def get_eligible_providers(
    payment_data,
    enabled_gateways: list[str],
) -> list[str]:
    eligible = []

    executable_gateways = set(GATEWAY_PROVIDERS)

    normalized_gateways = {
        gateway.strip().lower()
        for gateway in enabled_gateways
        if isinstance(gateway, str) and gateway.strip()
    }

    if (
        "razorpay" in normalized_gateways
        and "razorpay" in executable_gateways
    ):
        eligible.append("razorpay")

    if (
        "cashfree" in normalized_gateways
        and "cashfree" in executable_gateways
        and payment_data.currency.upper() == "INR"
        and payment_data.amount_minor >= 100
        and payment_data.customer_phone
    ):
        eligible.append("cashfree")

    if (
        "payu" in normalized_gateways
        and "payu" in executable_gateways
        and payment_data.currency.upper() == "INR"
        and payment_data.customer_phone
        and payment_data.customer_email
    ):
        eligible.append("payu")

    return eligible


def _routing_idempotency_key(
    merchant_id: UUID,
    original_key: str,
) -> str:
    value = f"{merchant_id}:{original_key}"

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _select_ranked_provider(
    candidates: list[dict],
    preferred_gateway: str | None,
) -> str:
    ranked_candidates = []

    for candidate in candidates:
        provider = str(
            candidate.get("provider", "")
        ).strip().lower()

        score = candidate.get("score")

        if not provider:
            continue

        if isinstance(score, bool) or not isinstance(
            score,
            (int, float),
        ):
            continue

        ranked_candidates.append((provider, float(score)))

    if not ranked_candidates:
        raise RoutingUnavailableError(
            "Aggregator returned candidates without valid scores"
        )

    highest_score = max(score for _, score in ranked_candidates)

    if preferred_gateway:
        for provider, score in ranked_candidates:
            if (
                provider == preferred_gateway
                and highest_score - score
                <= PREFERRED_GATEWAY_SCORE_TOLERANCE
            ):
                return provider

    for provider, score in ranked_candidates:
        if score == highest_score:
            return provider

    raise RoutingUnavailableError(
        "Unable to select a ranked gateway candidate"
    )


def select_gateway_provider(
    *,
    payment_id: UUID,
    merchant_id: UUID,
    payment_data,
    idempotency_key: str,
    enabled_gateways: list[str],
    preferred_gateway: str | None = None,
) -> str:
    eligible_providers = get_eligible_providers(
        payment_data,
        enabled_gateways,
    )

    if not eligible_providers:
        raise RoutingUnavailableError(
            "No enabled and executable payment providers are available"
        )

    if (
        preferred_gateway
        and preferred_gateway not in eligible_providers
    ):
        preferred_gateway = None

    request_body = {
        "payment_id": str(payment_id),
        "amount_minor": payment_data.amount_minor,
        "currency": payment_data.currency.upper(),
        "idempotency_key": _routing_idempotency_key(
            merchant_id,
            idempotency_key,
        ),
        "payment_method": getattr(payment_data, "payment_method", None),
        "bank_code": getattr(payment_data, "bank_code", None),
        "customer_phone": payment_data.customer_phone,
        "customer_email": payment_data.customer_email,
        "customer_name": getattr(payment_data, "customer_name", None),
    }

    params = [
        ("allowed_providers", provider)
        for provider in eligible_providers
    ]

    try:
        response = httpx.post(
            f"{AGGREGATOR_BASE_URL}/v1/router/decision",
            json=request_body,
            params=params,
            timeout=ROUTING_TIMEOUT_SECONDS,
        )

        response.raise_for_status()
        result = response.json()

        if not isinstance(result, dict):
            raise RoutingUnavailableError(
                "Aggregator returned an invalid decision response"
            )

        if str(result.get("payment_id")) != str(payment_id):
            raise RoutingUnavailableError(
                "Aggregator returned a decision for a different payment"
            )

        candidates = result.get("candidates")

        if not isinstance(candidates, list):
            raise RoutingUnavailableError(
                "Aggregator returned an invalid candidate list"
            )

        valid_candidates = []

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            provider = str(
                candidate.get("provider", "")
            ).strip().lower()

            if provider in eligible_providers:
                valid_candidates.append(candidate)

        if not valid_candidates:
            raise RoutingUnavailableError(
                "Aggregator returned no eligible gateway candidates"
            )

        selected_provider = _select_ranked_provider(
            candidates=valid_candidates,
            preferred_gateway=preferred_gateway,
        )

        logger.info(
            "Gateway selected for payment %s: %s",
            payment_id,
            selected_provider,
        )

        return selected_provider

    except RoutingUnavailableError:
        raise

    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.exception(
            "Automatic gateway selection failed for payment %s",
            payment_id,
        )

        raise RoutingUnavailableError(
            "Automatic gateway selection is temporarily unavailable"
        ) from exc
