
"""Add customer email to payments.

Revision ID: 2f317c629df8
Revises: f6a2c9d81430
"""

from alembic import op
import sqlalchemy as sa


revision = "2f317c629df8"
down_revision = "f6a2c9d81430"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "customer_email",
            sa.String(length=254),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "payments",
        "customer_email",
    )
