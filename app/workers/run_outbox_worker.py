
import logging
import signal
import time

from app.core.database import SessionLocal
from app.workers.kafka_publisher import KafkaEventPublisher
from app.workers.outbox_worker import process_outbox_batch


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
POLL_INTERVAL_SECONDS = 2

running = True


def stop_worker(signum, frame):
    global running
    logger.info("Shutdown signal received. Stopping worker...")
    running = False


signal.signal(signal.SIGTERM, stop_worker)
signal.signal(signal.SIGINT, stop_worker)


def main():
    global running

    publisher = KafkaEventPublisher()
    logger.info("LedgerCore outbox worker starting")

    try:
        publisher.start()

        logger.info("LedgerCore outbox worker started")

        while running:
            db = SessionLocal()

            try:
                result = process_outbox_batch(
                    db=db,
                    batch_size=BATCH_SIZE,
                    kafka_publisher=publisher,
                )

                if any(result.values()):
                    logger.info("Batch result: %s", result)

            except Exception:
                logger.exception("Unexpected outbox worker error")

            finally:
                db.close()

            if running:
                time.sleep(POLL_INTERVAL_SECONDS)

    except Exception:
        logger.exception("LedgerCore outbox worker failed to start or run")

    finally:
        publisher.stop()
        logger.info("LedgerCore outbox worker stopped")


if __name__ == "__main__":
    main()
