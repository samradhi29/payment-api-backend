
"""Add processing leases to webhook deliveries.

Revision ID: c7e42a91b605
Revises: f3a91c7d2e60
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c7e42a91b605"
down_revision = "f3a91c7d2e60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "claim_token",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    op.create_index(
        "idx_webhook_deliveries_expired_lease",
        "webhook_deliveries",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_webhook_deliveries_expired_lease",
        table_name="webhook_deliveries",
    )

    op.drop_column("webhook_deliveries", "claim_token")
    op.drop_column("webhook_deliveries", "lease_expires_at")
    op.drop_column("webhook_deliveries", "processing_started_at")
