
"""Add Cashfree payment fields.

Revision ID: f6a2c9d81430
Revises: c7e42a91b605
"""

from alembic import op
import sqlalchemy as sa


revision = "f6a2c9d81430"
down_revision = "c7e42a91b605"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "customer_phone",
            sa.String(length=20),
            nullable=True,
        ),
    )

    op.add_column(
        "payments",
        sa.Column(
            "provider_session_id",
            sa.String(length=255),
            nullable=True,
        ),
    )

    op.create_index(
        "uq_payments_provider_session_id",
        "payments",
        ["provider", "provider_session_id"],
        unique=True,
        postgresql_where=sa.text("provider_session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_payments_provider_session_id",
        table_name="payments",
    )

    op.drop_column("payments", "provider_session_id")
    op.drop_column("payments", "customer_phone")
