
import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from app.config import settings


logger = logging.getLogger(__name__)


class KafkaMetricsConsumer:
    def __init__(self, metrics_store):
        self.metrics_store = metrics_store
        self.consumer = None
        self.running = False

    async def start(self):
        if self.consumer is not None:
            raise RuntimeError("Kafka consumer is already initialized")

        consumer = AIOKafkaConsumer(
            settings.kafka_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )

        try:
            await consumer.start()
        except Exception:
            try:
                await consumer.stop()
            except Exception:
                logger.exception(
                    "Failed to clean up Kafka consumer after startup failure"
                )
            raise

        self.consumer = consumer
        self.running = True

        logger.info("Kafka metrics consumer started")

    async def stop(self):
        self.running = False

        consumer = self.consumer
        self.consumer = None

        if consumer is not None:
            try:
                await consumer.stop()
            finally:
                logger.info("Kafka metrics consumer stopped")

    async def process_message(self, message):
        raw_value = (
            message
            if isinstance(message, bytes)
            else message.value
        )

        if not isinstance(raw_value, (bytes, bytearray)):
            raise TypeError("Kafka event payload must contain bytes")

        event = json.loads(raw_value.decode("utf-8"))

        if not isinstance(event, dict):
            raise ValueError("Kafka event must be a JSON object")

        await self.metrics_store.record(event)

    async def _commit_message(self, message):
        if self.consumer is None:
            raise RuntimeError("Kafka consumer is not available")

        partition = TopicPartition(
            message.topic,
            message.partition,
        )

        # Kafka commits the next offset to consume, not the
        # offset of the message that was just processed.
        offsets = {
            partition: OffsetAndMetadata(
                message.offset + 1,
                "",
            )
        }

        await self.consumer.commit(offsets)

    async def consume(self):
        if self.consumer is None or not self.running:
            raise RuntimeError(
                "Kafka consumer has not been started"
            )

        async for message in self.consumer:
            if not self.running:
                break

            while self.running:
                try:
                    await self.process_message(message)
                    break

                except (ValueError, TypeError, KeyError):
                    logger.exception(
                        "Discarding invalid metrics event "
                        "at partition %s offset %s",
                        message.partition,
                        message.offset,
                    )

                    # Invalid messages are deliberately discarded.
                    # Commit only this message's offset.
                    break

                except asyncio.CancelledError:
                    raise

                except Exception:
                    logger.exception(
                        "Metrics event processing failed at "
                        "partition %s offset %s; retrying",
                        message.partition,
                        message.offset,
                    )

                    # Do not advance to another message. Otherwise,
                    # a later commit could skip this failed event.
                    await asyncio.sleep(1)

            if not self.running:
                break

            # If commit fails, let the task fail rather than
            # processing later messages and risking data loss.
            await self._commit_message(message)

