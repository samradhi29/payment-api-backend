
import asyncio
import json
import logging
import threading

from aiokafka import AIOKafkaProducer

from app.core.config import settings


logger = logging.getLogger(__name__)


class KafkaEventPublisher:
    def __init__(self):
        self.producer = None
        self.loop = None
        self.thread = None
        self.ready = threading.Event()
        self.startup_error = None

    async def _start_producer(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda value: json.dumps(
                value
            ).encode("utf-8"),
        )

        await self.producer.start()

    async def _publish(self, event: dict):
        if self.producer is None:
            raise RuntimeError("Kafka producer is not started")

        event_id = event.get("event_id")

        await asyncio.wait_for(
            self.producer.send_and_wait(
                settings.KAFKA_TOPIC,
                value=event,
                key=event_id.encode("utf-8") if event_id else None,
            ),
            timeout=5.0,
        )

    def _run_event_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        try:
            self.loop.run_until_complete(self._start_producer())
            self.ready.set()
            self.loop.run_forever()

        except Exception as exc:
            self.startup_error = exc
            self.ready.set()
            logger.exception("Kafka publisher event loop failed")

        finally:
            if self.producer is not None:
                try:
                    self.loop.run_until_complete(
                        self.producer.stop()
                    )
                except Exception:
                    logger.exception("Failed to stop Kafka producer")

            pending = asyncio.all_tasks(self.loop)

            for task in pending:
                task.cancel()

            if pending:
                self.loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

            self.loop.close()

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return

        self.ready.clear()
        self.startup_error = None

        self.thread = threading.Thread(
            target=self._run_event_loop,
            name="ledgercore-kafka-publisher",
            daemon=True,
        )

        self.thread.start()

        if not self.ready.wait(timeout=15):
            raise TimeoutError("Kafka producer startup timed out")

        if self.startup_error is not None:
            raise RuntimeError(
                "Kafka producer failed to start"
            ) from self.startup_error

        logger.info("LedgerCore Kafka publisher started")

    def publish(self, event: dict):
        if self.loop is None or self.thread is None:
            raise RuntimeError("Kafka publisher has not been started")

        if not self.thread.is_alive() or self.loop.is_closed():
            raise RuntimeError("Kafka publisher event loop is not running")

        future = asyncio.run_coroutine_threadsafe(
            self._publish(event),
            self.loop,
        )

        future.result(timeout=7.0)

    def stop(self):
        if self.loop is None or self.thread is None:
            return

        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)

        if self.thread.is_alive():
            logger.error("Kafka publisher thread did not stop cleanly")
            return

        logger.info("LedgerCore Kafka publisher stopped")

        self.loop = None
        self.thread = None
        self.producer = None
