
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class RouterPaymentRequest(BaseModel):
    payment_id: UUID
    amount_minor: int = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    idempotency_key: str = Field(min_length=8, max_length=255)
    payment_method: str | None = Field(default=None, min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    bank_code: str | None = Field(default=None, min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    customer_phone: str | None = None
    customer_email: str | None = None
    customer_name: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("payment_method", "bank_code")
    @classmethod
    def normalize_routing_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        return normalized or None

    @field_validator("customer_phone", "customer_email", "customer_name")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RouterPaymentResult(BaseModel):
    payment_id: UUID
    provider: str
    provider_order_id: str
    amount_minor: int
    currency: str
    status: str
    simulated: bool
    provider_session_id: str | None = None
    checkout_data: dict = Field(default_factory=dict)


class RouterDecisionResponse(BaseModel):
    selected_provider: str
    score: float
    score_breakdown: dict[str, float]
    result: RouterPaymentResult
    attempted_gateways: list[str]
    retries_used: int
