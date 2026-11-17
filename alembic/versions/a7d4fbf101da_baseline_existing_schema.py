
from pathlib import Path
from typing import Sequence, Union

from alembic import op


revision: str = "a7d4fbf101da"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Create the initial LedgerCore database schema.
    """
    schema_path = Path(__file__).resolve().parents[2] / "schema.sql"

    if not schema_path.exists():
        raise FileNotFoundError(
            f"Initial schema file not found: {schema_path}"
        )

    schema_sql = schema_path.read_text(encoding="utf-8")

    connection = op.get_bind()

    # Prevent Psycopg from interpreting PostgreSQL '%' characters
    # as parameter placeholders.
    connection.exec_driver_sql(
        schema_sql,
        execution_options={"no_parameters": True},
    )


def downgrade() -> None:
    raise NotImplementedError(
        "The initial LedgerCore schema cannot be downgraded automatically."
    )
