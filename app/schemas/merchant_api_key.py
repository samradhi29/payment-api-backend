
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MerchantAPIKeyCreate(BaseModel):
    label: str = Field(
        min_length=1,
        max_length=100,
    )
    expires_at: datetime | None = None


class MerchantAPIKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    key_prefix: str
    is_active: bool
    expires_at: datetime | None
    created_at: datetime


class MerchantAPIKeyCreatedResponse(MerchantAPIKeyResponse):
    # This is returned only by the creation endpoint.
    secret: str
