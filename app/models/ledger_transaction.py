
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    Text,
    CHAR,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"

    __table_args__ = (
        UniqueConstraint(
            "type",
            "reference_id",
            name="unique_ledger_reference",
        ),
        Index("idx_ledger_tx_payment", "payment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id"),
    )

    reverses_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_transactions.id"),
        unique=True,
    )

    type: Mapped[str] = mapped_column(
        ENUM(
            "payment",
            "refund",
            "reversal",
            "adjustment",
            name="ledger_transaction_type",
            create_type=False,
        ),
        nullable=False,
    )

    reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        CHAR(3),
        nullable=False,
        server_default=text("'INR'"),
    )

    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
