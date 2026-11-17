
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
    field_validator,
)


PaymentProvider = Literal[
    "razorpay",
    "cashfree",
    "payu",
]

PaymentProviderInput = Literal[
    "razorpay",
    "cashfree",
    "payu",
    "auto",
]


class PaymentCreate(BaseModel):
    customer_account_id: UUID
    amount_minor: int = Field(gt=0)
    currency: str = Field(
        default="INR",
        pattern=r"^[A-Z]{3}$",
    )
    provider: PaymentProviderInput = "razorpay"

    payment_method: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    bank_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    customer_phone: str | None = Field(
        default=None,
        min_length=10,
        max_length=16,
        pattern=r"^\+?[1-9]\d{9,14}$",
    )

    customer_email: str | None = Field(
        default=None,
        min_length=5,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )

    @field_validator("payment_method", "bank_code")
    @classmethod
    def normalize_routing_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        return normalized or None

    @model_validator(mode="after")
    def validate_provider_requirements(self):
        if self.provider == "cashfree":
            if self.currency != "INR":
                raise ValueError(
                    "Cashfree currently supports INR only in LedgerCore."
                )

            if self.amount_minor < 100:
                raise ValueError(
                    "Cashfree payments must be at least INR 1."
                )

            if not self.customer_phone:
                raise ValueError(
                    "Customer phone is required for Cashfree payments."
                )

        if self.provider == "payu":
            if self.currency != "INR":
                raise ValueError(
                    "PayU currently supports INR only in LedgerCore."
                )

            if not self.customer_phone:
                raise ValueError(
                    "Customer phone is required for PayU payments."
                )

            if not self.customer_email:
                raise ValueError(
                    "Customer email is required for PayU payments."
                )

        # Auto-routing evaluates provider eligibility later.
        return self


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_account_id: UUID
    merchant_account_id: UUID
    amount_minor: int
    currency: str
    provider: PaymentProviderInput
    payment_method: str | None = None
    bank_code: str | None = None
    status: Literal[
        "pending",
        "processing",
        "succeeded",
        "failed",
        "needs_review",
    ]
    created_at: datetime


class RazorpayPaymentVerification(BaseModel):
    razorpay_order_id: str = Field(
        min_length=1,
        max_length=255,
    )
    razorpay_payment_id: str = Field(
        min_length=1,
        max_length=255,
    )
    razorpay_signature: str = Field(
        min_length=1,
        max_length=255,
    )
