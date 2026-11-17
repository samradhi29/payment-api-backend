
"""Add separate provider order ID to payments."""

from alembic import op
import sqlalchemy as sa


revision = "b82c9e41d601"
down_revision = "a7d4fbf101da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "provider_order_id",
            sa.String(length=255),
            nullable=True,
        ),
    )

    # Move existing Razorpay order IDs into the new column.
    op.execute(
        """
        UPDATE payments
        SET provider_order_id = provider_payment_id,
            provider_payment_id = NULL
        WHERE provider = 'razorpay'
          AND provider_payment_id IS NOT NULL
        """
    )

    op.create_index(
        "uq_provider_order",
        "payments",
        ["provider", "provider_order_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_provider_order",
        table_name="payments",
    )

    op.drop_column(
        "payments",
        "provider_order_id",
    )
