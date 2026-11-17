
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    CHAR,
    Index,
    text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Payment(Base):
    __tablename__ = "payments"

    __table_args__ = (
        Index("idx_payments_created_at", "created_at"),
        Index("idx_payments_status", "status"),
        Index(
            "uq_provider_order",
            "provider",
            "provider_order_id",
            unique=True,
        ),
        Index(
            "uq_provider_payment",
            "provider",
            "provider_payment_id",
            unique=True,
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
        Index(
            "uq_payments_provider_session_id",
            "provider",
            "provider_session_id",
            unique=True,
            postgresql_where=text(
                "provider_session_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    customer_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=False,
    )

    merchant_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        CHAR(3),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        ENUM(
            "pending",
            "processing",
            "succeeded",
            "failed",
            "needs_review",
            name="payment_status",
            create_type=False,
        ),
        nullable=False,
    )

    # Selected payment gateway
    provider: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Routing hints used by the intelligent gateway scorer
    payment_method: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    bank_code: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # Customer contact details
    customer_phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    customer_email: Mapped[str | None] = mapped_column(
        String(254),
        nullable=True,
    )

    # Generic gateway order identifier
    provider_order_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Generic gateway payment identifier
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Gateway checkout session
    provider_session_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    response: Mapped[dict | None] = mapped_column(JSONB)

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
