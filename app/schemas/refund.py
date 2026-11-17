
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


RefundStatus = Literal[
    "pending",
    "processing",
    "succeeded",
    "failed",
    "needs_review",
]


class RefundCreateRequest(BaseModel):
    amount_minor: int = Field(
        gt=0,
        description="Refund amount in the smallest currency unit.",
    )

    reason: str | None = Field(
        default=None,
        max_length=250,
        description="Optional reason for the refund.",
    )


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_id: UUID
    amount_minor: int
    currency: str
    provider: str
    provider_refund_id: str | None = None
    status: RefundStatus
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class RefundListResponse(BaseModel):
    items: list[RefundResponse]
    total: int
    limit: int
    offset: int
