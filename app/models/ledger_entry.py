
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


entry_side_enum = ENUM(
    "debit",
    "credit",
    name="entry_side",
    create_type=False,
)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    __table_args__ = (
        Index(
            "idx_ledger_entries_account_id",
            "account_id",
        ),
        Index(
            "idx_ledger_entries_account_created_at",
            "account_id",
            "created_at",
        ),
        Index(
            "idx_ledger_entries_transaction_id",
            "transaction_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_transactions.id"),
        nullable=False,
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=False,
    )

    side: Mapped[str] = mapped_column(
        entry_side_enum,
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
