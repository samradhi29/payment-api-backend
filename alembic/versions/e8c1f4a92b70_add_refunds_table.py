
"""Add refunds table.

Revision ID: e8c1f4a92b70
Revises: 9c4e2a71b6d3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e8c1f4a92b70"
down_revision = "9c4e2a71b6d3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "refunds",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "merchant_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=150)),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("failure_reason", sa.String(length=500)),
        sa.Column("response", sa.JSON()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_refunds_amount_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', "
            "'failed', 'needs_review')",
            name="ck_refunds_status_valid",
        ),
        sa.UniqueConstraint(
            "merchant_account_id",
            "idempotency_key",
            name="uq_refunds_merchant_idempotency",
        ),
    )

    op.create_index(
        "ix_refunds_payment_id",
        "refunds",
        ["payment_id"],
    )

    op.create_index(
        "ix_refunds_merchant_account_id",
        "refunds",
        ["merchant_account_id"],
    )

    op.create_index(
        "ix_refunds_payment_created",
        "refunds",
        ["payment_id", "created_at"],
    )

    op.create_index(
        "ix_refunds_status_created",
        "refunds",
        ["status", "created_at"],
    )

    op.create_index(
        "uq_refunds_provider_refund_id",
        "refunds",
        ["provider", "provider_refund_id"],
        unique=True,
        postgresql_where=sa.text("provider_refund_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index(
        "uq_refunds_provider_refund_id",
        table_name="refunds",
    )
    op.drop_index(
        "ix_refunds_status_created",
        table_name="refunds",
    )
    op.drop_index(
        "ix_refunds_payment_created",
        table_name="refunds",
    )
    op.drop_index(
        "ix_refunds_merchant_account_id",
        table_name="refunds",
    )
    op.drop_index(
        "ix_refunds_payment_id",
        table_name="refunds",
    )

    op.drop_table("refunds")
