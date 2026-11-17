
"""Add merchant profiles.

Revision ID: 9c4e2a71b6d3
Revises: 2f317c629df8
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "9c4e2a71b6d3"
down_revision = "2f317c629df8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "merchant_profiles",
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "enabled_gateways",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "preferred_gateway",
            sa.String(length=30),
            nullable=True,
        ),
        sa.Column(
            "smart_routing_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )

    # Create profiles only for accounts that already have merchant API keys.
    # Do not infer merchant identity from accounting account types.
    op.execute(
        """
        INSERT INTO merchant_profiles (account_id)
        SELECT DISTINCT account_id
        FROM merchant_api_keys
        ON CONFLICT (account_id) DO NOTHING
        """
    )


def downgrade():
    op.drop_table("merchant_profiles")
