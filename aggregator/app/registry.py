
import os

from app.adapters.base import GatewayAdapter
from app.adapters.cashfree import CashfreeAdapter
from app.adapters.payu import PayUAdapter
from app.adapters.razorpay import RazorpayAdapter
from app.adapters.simulated import SimulatedGatewayAdapter


def _mock_adapter(name: str, env_prefix: str) -> GatewayAdapter:
    mode = os.getenv(f"{env_prefix}_MODE", "success")
    delay_ms = int(os.getenv(f"{env_prefix}_DELAY_MS", "0"))

    return SimulatedGatewayAdapter(
        provider_name=name,
        mode=mode,
        delay_ms=delay_ms,
    )


def build_registry() -> dict[str, GatewayAdapter]:
    return {
        "razorpay": RazorpayAdapter(),
        "cashfree": CashfreeAdapter(),
        "payu": PayUAdapter(),
        "mock_alpha": _mock_adapter("mock_alpha", "MOCK_ALPHA"),
        "mock_beta": _mock_adapter("mock_beta", "MOCK_BETA"),
        "mock_gamma": _mock_adapter("mock_gamma", "MOCK_GAMMA"),
        "mock_delta": _mock_adapter("mock_delta", "MOCK_DELTA"),
    }
