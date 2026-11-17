
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


SUPPORTED_WEBHOOK_EVENTS = {
    "payment.created",
    "payment.succeeded",
    "payment.failed",
}


class WebhookEndpointCreate(BaseModel):
    url: HttpUrl
    subscribed_events: list[str] = Field(min_length=1, max_length=10)


class WebhookEndpointResponse(BaseModel):
    id: UUID
    merchant_account_id: UUID
    url: str
    subscribed_events: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    # Returned only during endpoint creation.
    secret: str


class WebhookEndpointStatusResponse(BaseModel):
    id: UUID
    is_active: bool
    message: str
