
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class GatewayOrder:
    provider: str
    provider_order_id: str
    amount_minor: int
    currency: str
    provider_session_id: str | None = None
    checkout_data: dict[str, Any] | None = None


@dataclass
class GatewayPayment:
    provider: str
    provider_payment_id: str
    provider_order_id: str
    amount_minor: int
    currency: str
    status: str
    captured: bool


@dataclass
class GatewayRefund:
    provider: str
    provider_refund_id: str
    provider_payment_id: str | None
    provider_order_id: str | None
    amount_minor: int
    currency: str
    status: str
    raw_response: dict[str, Any] | None = None


class GatewayError(Exception):
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


class PaymentGatewayAdapter(ABC):

    @abstractmethod
    def create_order(
        self,
        payment_id: str,
        amount_minor: int,
        currency: str,
        customer_phone: str | None = None,
    ) -> GatewayOrder:
        """Create a payment order with the provider."""
        raise NotImplementedError

    @abstractmethod
    def fetch_payment(
        self,
        provider_order_id: str,
    ) -> GatewayPayment:
        """Fetch payment information using the provider order ID."""
        raise NotImplementedError

    def create_refund(
        self,
        *,
        provider_payment_id: str | None,
        provider_order_id: str | None,
        refund_id: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
        reason: str | None = None,
    ) -> GatewayRefund:
        """
        Initiate a refund.

        Providers must implement this method before refunds can be
        initiated through that adapter.
        """
        raise GatewayError(
            f"Refund creation is not implemented for {self.__class__.__name__}."
        )

    def fetch_refund(
        self,
        *,
        provider_payment_id: str | None,
        provider_order_id: str | None,
        provider_refund_id: str | None,
        refund_id: str,
    ) -> GatewayRefund:
        """
        Fetch the current status of an existing refund.

        This method must never initiate a new refund.
        """
        raise GatewayError(
            f"Refund lookup is not implemented for {self.__class__.__name__}."
        )
