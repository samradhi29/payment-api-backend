
"""Add webhook endpoints and delivery tracking."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d91f3a7c2b10"
down_revision = "b82c9e41d601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "merchant_account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "subscribed_events",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("TRUE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["merchant_account_id"],
            ["accounts.id"],
            name="fk_webhook_endpoints_merchant_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_account_id",
            "url",
            name="uq_webhook_endpoint_merchant_url",
        ),
    )

    op.create_index(
        "idx_webhook_endpoints_merchant",
        "webhook_endpoints",
        ["merchant_account_id"],
    )

    op.create_index(
        "idx_webhook_endpoints_active",
        "webhook_endpoints",
        ["is_active"],
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "outbox_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "webhook_endpoint_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_response_status", sa.Integer(), nullable=True),
        sa.Column("last_response_body", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_webhook_delivery_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'dead_letter')",
            name="ck_webhook_delivery_status",
        ),
        sa.ForeignKeyConstraint(
            ["outbox_event_id"],
            ["outbox_events.id"],
            name="fk_webhook_deliveries_outbox_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["webhook_endpoint_id"],
            ["webhook_endpoints.id"],
            name="fk_webhook_deliveries_endpoint",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outbox_event_id",
            "webhook_endpoint_id",
            name="uq_webhook_delivery_event_endpoint",
        ),
    )

    op.create_index(
        "idx_webhook_deliveries_pending",
        "webhook_deliveries",
        ["available_at", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_index(
        "idx_webhook_deliveries_endpoint",
        "webhook_deliveries",
        ["webhook_endpoint_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_webhook_deliveries_endpoint",
        table_name="webhook_deliveries",
    )
    op.drop_index(
        "idx_webhook_deliveries_pending",
        table_name="webhook_deliveries",
    )
    op.drop_table("webhook_deliveries")

    op.drop_index(
        "idx_webhook_endpoints_active",
        table_name="webhook_endpoints",
    )
    op.drop_index(
        "idx_webhook_endpoints_merchant",
        table_name="webhook_endpoints",
    )
    op.drop_table("webhook_endpoints")
