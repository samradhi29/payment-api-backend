
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    __table_args__ = (
        UniqueConstraint(
            "merchant_account_id",
            "url",
            name="uq_webhook_endpoint_merchant_url",
        ),
        Index(
            "idx_webhook_endpoints_merchant",
            "merchant_account_id",
        ),
        Index(
            "idx_webhook_endpoints_active",
            "is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    merchant_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    # This column will contain an encrypted signing secret.
    # Never store the raw signing secret here.
    secret_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    subscribed_events: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
