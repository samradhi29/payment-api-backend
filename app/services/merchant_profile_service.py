
from sqlalchemy.orm import Session

from app.models.merchant_profile import MerchantProfile


SUPPORTED_GATEWAYS = {
    "razorpay",
    "cashfree",
    "payu",
}


class MerchantProfileError(Exception):
    """Raised when merchant profile or gateway configuration is invalid."""


def get_merchant_profile(
    db: Session,
    merchant_id,
) -> MerchantProfile:
    profile = db.get(
        MerchantProfile,
        merchant_id,
    )

    if profile is None:
        raise MerchantProfileError(
            "Merchant profile is not configured"
        )

    if not profile.is_active:
        raise MerchantProfileError(
            "Merchant account is inactive"
        )

    return profile


def get_enabled_gateways(
    profile: MerchantProfile,
) -> list[str]:
    configured_gateways = profile.enabled_gateways or []

    if not isinstance(configured_gateways, list):
        raise MerchantProfileError(
            "Invalid merchant gateway configuration"
        )

    enabled_gateways = []

    for gateway in configured_gateways:
        if not isinstance(gateway, str):
            raise MerchantProfileError(
                "Invalid gateway entry in merchant configuration"
            )

        normalized_gateway = gateway.strip().lower()

        if normalized_gateway not in SUPPORTED_GATEWAYS:
            raise MerchantProfileError(
                f"Unsupported configured gateway: {normalized_gateway}"
            )

        if normalized_gateway not in enabled_gateways:
            enabled_gateways.append(normalized_gateway)

    return enabled_gateways


def validate_merchant_gateway(
    profile: MerchantProfile,
    provider: str,
) -> str:
    if not isinstance(provider, str):
        raise MerchantProfileError(
            "Invalid payment provider"
        )

    normalized_provider = provider.strip().lower()

    if normalized_provider not in SUPPORTED_GATEWAYS:
        raise MerchantProfileError(
            "Unsupported payment provider"
        )

    enabled_gateways = get_enabled_gateways(profile)

    if normalized_provider not in enabled_gateways:
        raise MerchantProfileError(
            f"Payment provider '{normalized_provider}' "
            "is not enabled for this merchant"
        )

    return normalized_provider


def get_merchant_routing_preferences(
    profile: MerchantProfile,
) -> tuple[list[str], str | None]:
    if not profile.smart_routing_enabled:
        raise MerchantProfileError(
            "Smart routing is not enabled for this merchant"
        )

    enabled_gateways = get_enabled_gateways(profile)

    if not enabled_gateways:
        raise MerchantProfileError(
            "At least one gateway must be enabled for smart routing"
        )

    preferred_gateway = profile.preferred_gateway

    if preferred_gateway is not None:
        if not isinstance(preferred_gateway, str):
            raise MerchantProfileError(
                "Invalid merchant preferred gateway"
            )

        preferred_gateway = preferred_gateway.strip().lower()

        if preferred_gateway not in SUPPORTED_GATEWAYS:
            raise MerchantProfileError(
                "Merchant preferred gateway is unsupported"
            )

        if preferred_gateway not in enabled_gateways:
            raise MerchantProfileError(
                "Merchant preferred gateway is not enabled"
            )

    return enabled_gateways, preferred_gateway
