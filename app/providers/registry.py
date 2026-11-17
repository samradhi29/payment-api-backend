
from app.providers.base import GatewayError, PaymentGatewayAdapter
from app.providers.cashfree import CashfreeAdapter
from app.providers.razorpay import RazorpayAdapter
from app.providers.payu import PayUAdapter


GATEWAY_PROVIDERS = {
    "razorpay": RazorpayAdapter,
    "cashfree": CashfreeAdapter,
    "payu": PayUAdapter,
}


def get_gateway_adapter(
    provider: str,
) -> PaymentGatewayAdapter:
    if not isinstance(provider, str):
        raise GatewayError("Invalid payment provider")

    normalized_provider = provider.strip().lower()

    adapter_class = GATEWAY_PROVIDERS.get(
        normalized_provider
    )

    if adapter_class is None:
        raise GatewayError(
            f"Unsupported payment provider: {normalized_provider}"
        )

    return adapter_class()
