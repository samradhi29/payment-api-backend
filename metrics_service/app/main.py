import asyncio
import logging
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query

from app.config import settings
from app.consumer import KafkaMetricsConsumer
from app.redis_store import RedisMetricsStore
from app.snapshot_store import PostgresSnapshotStore


logger = logging.getLogger(__name__)

MIN_CONSUMER_RETRY_SECONDS = 1
MAX_CONSUMER_RETRY_SECONDS = 30


async def snapshot_loop(metrics_store, snapshot_store):
    while True:
        await asyncio.sleep(settings.snapshot_interval_seconds)

        try:
            metrics = await metrics_store.get_all_metrics()
            count = await snapshot_store.save_snapshot(metrics)

            logger.info("Saved %s gateway metric snapshots", count)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception("Failed to save metrics snapshot")


async def consumer_supervisor(consumer, app):
    """Keep the API alive and reconnect the Kafka consumer after failures."""
    retry_delay = MIN_CONSUMER_RETRY_SECONDS

    while True:
        try:
            await consumer.start()
            retry_delay = MIN_CONSUMER_RETRY_SECONDS
            app.state.kafka_connected = True
            logger.info("Kafka metrics consumer is connected")

            await consumer.consume()

            if consumer.running:
                raise RuntimeError(
                    "Kafka metrics consumer stopped unexpectedly"
                )

        except asyncio.CancelledError:
            raise

        except Exception:
            app.state.kafka_connected = False
            logger.exception(
                "Kafka metrics consumer stopped; reconnecting in %s seconds",
                retry_delay,
            )

        finally:
            app.state.kafka_connected = False
            try:
                await consumer.stop()
            except Exception:
                logger.exception("Failed to stop Kafka consumer cleanly")

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, MAX_CONSUMER_RETRY_SECONDS)


async def _cancel_task(task, task_name: str):
    if task is None:
        return

    task.cancel()

    try:
        await task

    except asyncio.CancelledError:
        pass

    except Exception:
        logger.exception("Error while shutting down %s", task_name)


def create_app(
    metrics_store=None,
    snapshot_store=None,
    start_background=True,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis_client = None
        db_pool = None
        consumer = None
        consumer_task = None
        snapshot_task = None

        store = metrics_store
        snapshots = snapshot_store

        app.state.kafka_connected = False
        app.state.kafka_required = bool(start_background)
        app.state.dependencies_ready = False

        try:
            if store is None:
                redis_client = redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                )
                await redis_client.ping()
                store = RedisMetricsStore(redis_client)

            if snapshots is None:
                db_pool = await asyncpg.create_pool(
                    dsn=settings.database_url,
                    min_size=1,
                    max_size=5,
                )
                snapshots = PostgresSnapshotStore(db_pool)
                await snapshots.initialize()

            app.state.metrics_store = store
            app.state.snapshot_store = snapshots
            app.state.redis_client = redis_client
            app.state.db_pool = db_pool
            app.state.dependencies_ready = True

            if start_background:
                consumer = KafkaMetricsConsumer(store)
                consumer_task = asyncio.create_task(
                    consumer_supervisor(consumer, app),
                    name="kafka-metrics-consumer-supervisor",
                )
                snapshot_task = asyncio.create_task(
                    snapshot_loop(store, snapshots),
                    name="metrics-snapshot-loop",
                )

            yield

        finally:
            app.state.dependencies_ready = False

            # Stop background tasks before closing their dependencies.
            await _cancel_task(snapshot_task, "metrics snapshot task")
            await _cancel_task(consumer_task, "Kafka consumer supervisor")

            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:
                    logger.exception("Failed to stop Kafka metrics consumer")

            if db_pool is not None:
                try:
                    await db_pool.close()
                except Exception:
                    logger.exception("Failed to close metrics PostgreSQL pool")

            if redis_client is not None:
                try:
                    await redis_client.aclose()
                except Exception:
                    logger.exception("Failed to close metrics Redis connection")

    app = FastAPI(
        title="Payment Metrics Service",
        version="1.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health():
        # Liveness only: transient Kafka outages should not restart the API.
        return {"status": "ok", "service": "metrics-service"}

    @app.get("/ready")
    async def readiness():
        if not getattr(app.state, "dependencies_ready", False):
            raise HTTPException(
                status_code=503,
                detail="Metrics service dependencies are not ready",
            )

        if (
            getattr(app.state, "kafka_required", False)
            and not getattr(app.state, "kafka_connected", False)
        ):
            raise HTTPException(
                status_code=503,
                detail="Kafka consumer is not connected",
            )

        return {"status": "ready", "service": "metrics-service"}

    @app.get("/v1/metrics")
    async def all_metrics():
        store = getattr(app.state, "metrics_store", None)

        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Metrics store is unavailable",
            )

        return await store.get_all_metrics()

    @app.get("/v1/metrics/{gateway}")
    async def gateway_metrics(
        gateway: str,
        window: str = Query(default="1m"),
    ):
        store = getattr(app.state, "metrics_store", None)

        if store is None:
            raise HTTPException(
                status_code=503,
                detail="Metrics store is unavailable",
            )

        try:
            return await store.get_metrics(gateway, window)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    return app


app = create_app()
