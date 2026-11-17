
import asyncio

from app.adapters.base import (
    GatewayAdapter,
    GatewayRejectedError,
    GatewayTimeoutError,
)
from app.models import RouterPaymentRequest, RouterPaymentResult


class SimulatedGatewayAdapter(GatewayAdapter):
    simulated = True

    def __init__(
        self,
        provider_name: str,
        mode: str = "success",
        delay_ms: int = 0,
    ):
        self.provider_name = provider_name
        self.mode = mode.lower()
        self.delay_ms = max(0, delay_ms)

        if self.mode not in {"success", "fail", "timeout"}:
            raise ValueError(
                f"Unsupported simulated gateway mode: {mode}"
            )

    async def create_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult:
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)

        if self.mode == "timeout":
            raise GatewayTimeoutError(
                f"Simulated timeout from {self.provider_name}"
            )

        if self.mode == "fail":
            raise GatewayRejectedError(
                f"Simulated definitive rejection from {self.provider_name}"
            )

        order_id = (
            f"sim_{self.provider_name}_{payment.payment_id.hex}"
        )

        return self.build_result(
            payment=payment,
            provider_order_id=order_id,
            status="created",
        )
