
import uuid
from datetime import datetime

from sqlalchemy import (
    Integer,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    CheckConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "idempotency_key",
            name="unique_scoped_idempotency_key",
        ),
        CheckConstraint(
            "scope_type IN ('customer', 'merchant')",
            name="ck_idempotency_keys_scope_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    scope_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'customer'"),
    )

    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id"),
    )

    response_status: Mapped[int | None] = mapped_column(Integer)

    response_body: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
