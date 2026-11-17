
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class Refund(Base):
    __tablename__ = "refunds"

    __table_args__ = (
        CheckConstraint(
            "amount_minor > 0",
            name="ck_refunds_amount_positive",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', "
            "'failed', 'needs_review')",
            name="ck_refunds_status_valid",
        ),
        UniqueConstraint(
            "merchant_account_id",
            "idempotency_key",
            name="uq_refunds_merchant_idempotency",
        ),
        Index(
            "ix_refunds_payment_created",
            "payment_id",
            "created_at",
        ),
        Index(
            "ix_refunds_status_created",
            "status",
            "created_at",
        ),
        Index(
            "uq_refunds_provider_refund_id",
            "provider",
            "provider_refund_id",
            unique=True,
            postgresql_where="provider_refund_id IS NOT NULL",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    payment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    merchant_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    amount_minor = Column(
        BigInteger,
        nullable=False,
    )

    currency = Column(
        String(3),
        nullable=False,
    )

    provider = Column(
        String(30),
        nullable=False,
    )

    provider_refund_id = Column(
        String(150),
        nullable=True,
    )

    idempotency_key = Column(
        String(255),
        nullable=False,
    )

    request_hash = Column(
        String(64),
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    failure_reason = Column(
        String(500),
        nullable=True,
    )

    response = Column(
        JSON,
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
