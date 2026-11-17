
from abc import ABC, abstractmethod

from app.models import RouterPaymentRequest, RouterPaymentResult


class GatewayAdapterError(Exception):
    pass


class GatewayRejectedError(GatewayAdapterError):
    """Provider definitively rejected the request without creating an order."""


class GatewayUnavailableError(GatewayAdapterError):
    """Provider availability or network failure; outcome may be uncertain."""


class GatewayTimeoutError(GatewayAdapterError):
    """Provider request timed out; payment outcome may be uncertain."""


class GatewayConfigurationError(GatewayAdapterError):
    """Gateway configuration is invalid or incomplete."""


class GatewayAdapter(ABC):
    provider_name: str
    simulated: bool = False

    @abstractmethod
    async def create_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult:
        raise NotImplementedError

    async def reconcile_order(
        self,
        payment: RouterPaymentRequest,
    ) -> RouterPaymentResult | None:
        raise GatewayConfigurationError(
            f"Reconciliation is not implemented for {self.provider_name}"
        )

    def build_result(
        self,
        payment: RouterPaymentRequest,
        provider_order_id: str,
        status: str = "created",
        provider_session_id: str | None = None,
        checkout_data: dict | None = None,
    ) -> RouterPaymentResult:
        return RouterPaymentResult(
            payment_id=payment.payment_id,
            provider=self.provider_name,
            provider_order_id=provider_order_id,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            status=status,
            simulated=self.simulated,
            provider_session_id=provider_session_id,
            checkout_data=checkout_data or {},
        )
