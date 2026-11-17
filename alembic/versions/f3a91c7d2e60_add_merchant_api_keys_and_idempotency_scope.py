
"""Add merchant API keys and idempotency scope type."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f3a91c7d2e60"
down_revision = "d91f3a7c2b10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "idempotency_keys",
        sa.Column(
            "scope_type",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'customer'"),
        ),
    )

    op.drop_constraint(
        "unique_scoped_idempotency_key",
        "idempotency_keys",
        type_="unique",
    )

    op.create_check_constraint(
        "ck_idempotency_keys_scope_type",
        "idempotency_keys",
        "scope_type IN ('customer', 'merchant')",
    )

    op.create_unique_constraint(
        "unique_scoped_idempotency_key",
        "idempotency_keys",
        ["scope_type", "scope_id", "idempotency_key"],
    )

    op.create_table(
        "merchant_api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=30), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_merchant_api_keys_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "key_hash",
            name="uq_merchant_api_keys_hash",
        ),
    )

    op.create_index(
        "idx_merchant_api_keys_account",
        "merchant_api_keys",
        ["account_id"],
    )


def downgrade():
    op.drop_index(
        "idx_merchant_api_keys_account",
        table_name="merchant_api_keys",
    )

    op.drop_table("merchant_api_keys")

    op.drop_constraint(
        "unique_scoped_idempotency_key",
        "idempotency_keys",
        type_="unique",
    )

    op.drop_constraint(
        "ck_idempotency_keys_scope_type",
        "idempotency_keys",
        type_="check",
    )

    op.create_unique_constraint(
        "unique_scoped_idempotency_key",
        "idempotency_keys",
        ["scope_id", "idempotency_key"],
    )

    op.drop_column("idempotency_keys", "scope_type")
