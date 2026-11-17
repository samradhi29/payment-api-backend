import json
from datetime import datetime, timezone


class PostgresSnapshotStore:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_metric_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    captured_at TIMESTAMPTZ NOT NULL,
                    gateway VARCHAR(100) NOT NULL,
                    "window" VARCHAR(10) NOT NULL,
                    metrics JSONB NOT NULL
                )
                """
            )

            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_gateway_metric_snapshots_lookup
                ON gateway_metric_snapshots
                (gateway, "window", captured_at DESC)
                """
            )

    async def save_snapshot(self, metrics: dict):
        captured_at = datetime.now(timezone.utc)
        records = []

        for gateway, windows in metrics.items():
            for window, values in windows.items():
                records.append(
                    (
                        captured_at,
                        gateway,
                        window,
                        json.dumps(values),
                    )
                )

        if not records:
            return 0

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.executemany(
                    """
                    INSERT INTO gateway_metric_snapshots
                    (captured_at, gateway, "window", metrics)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    records,
                )

        return len(records)
