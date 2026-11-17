"""Persist payment method and bank routing hints.

Revision ID: c8a4d1e6f203
Revises: e8c1f4a92b70
"""

from alembic import op


revision = "c8a4d1e6f203"
down_revision = "e8c1f4a92b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS supports both databases created from the current
    # schema.sql and databases upgraded from an earlier baseline.
    op.execute(
        "ALTER TABLE payments "
        "ADD COLUMN IF NOT EXISTS payment_method VARCHAR(30)"
    )
    op.execute(
        "ALTER TABLE payments "
        "ADD COLUMN IF NOT EXISTS bank_code VARCHAR(30)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS bank_code")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS payment_method")
