
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.database import Base

from app.models import (
    Account,
    Payment,
    IdempotencyKey,
    LedgerTransaction,
    LedgerEntry,
    OutboxEvent,
)

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url",
    settings.DATABASE_URL.replace("%", "%%")
)

target_metadata = Base.metadata


# This table belongs to the independent metrics service.
# It is managed by metrics_service/app/snapshot_store.py,
# not by the main API's SQLAlchemy models or Alembic migrations.
METRICS_OWNED_TABLES = {
    "gateway_metric_snapshots",
}


def include_object(object_, name, type_, reflected, compare_to):
    """
    Exclude metrics-service-owned tables and their indexes
    from the main API's Alembic autogeneration comparisons.
    """
    if type_ == "table" and name in METRICS_OWNED_TABLES:
        return False

    associated_table = getattr(object_, "table", None)

    if (
        associated_table is not None
        and associated_table.name in METRICS_OWNED_TABLES
    ):
        return False

    return True


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
