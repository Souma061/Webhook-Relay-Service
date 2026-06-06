import asyncio
import json
import logging
import os
import signal

from sqlalchemy import select, update

os.environ.setdefault("RELAY_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/webhook_relay")

from app.core.config import settings
from app.core.database import async_session_factory, init_db
from app.core.kafka import init_kafka, get_kafka, close_kafka
from app.models.event import Event
from app.models.outbox import OutboxRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s [outbox-relay] %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 10
POLL_INTERVAL_S = 1
MAX_ATTEMPTS = 10

_stop = asyncio.Event()


def _handle_signal(*_):
    logger.info("Shutdown signal received — stopping relay")
    _stop.set()


async def _publish(event_id: str, publish_key: str, publish_topic: str) -> None:
    producer = get_kafka()
    if producer is None:
        raise RuntimeError("Kafka producer not available")

    await producer.send_and_wait(
        publish_topic,
        value={
            "event_id": str(event_id),
            "endpoint_id": publish_key,
        },
        key=publish_key.encode(),
    )


async def run():
    await init_db()
    await init_kafka()

    logger.info("Outbox relay started (poll_interval=%ds, batch_size=%d)", POLL_INTERVAL_S, BATCH_SIZE)

    try:
        while not _stop.is_set():
            async with async_session_factory() as db:
                result = await db.execute(
                    select(OutboxRecord)
                    .where(OutboxRecord.status == "pending")
                    .order_by(OutboxRecord.created_at)
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
                records = result.scalars().all()

                if not records:
                    await asyncio.sleep(POLL_INTERVAL_S)
                    continue

                for record in records:
                    if _stop.is_set():
                        break

                    try:
                        await _publish(record.event_id, record.publish_key, record.publish_topic)
                    except Exception as exc:
                        record.attempts += 1
                        record.last_error = f"{type(exc).__name__}: {exc}"
                        if record.attempts >= MAX_ATTEMPTS:
                            record.status = "failed"
                            logger.error(
                                "Outbox %s for event %s failed after %d attempts: %s",
                                record.id, record.event_id, record.attempts, exc,
                            )
                        else:
                            logger.warning(
                                "Publish failed for outbox %s (event %s, attempt %d/%d): %s",
                                record.id, record.event_id, record.attempts, MAX_ATTEMPTS, exc,
                            )
                    else:
                        record.status = "completed"
                        ev = await db.get(Event, record.event_id)
                        if ev is not None:
                            ev.status = "queued"
                        logger.info(
                            "Published event %s to Kafka (topic=%s, outbox=%s)",
                            record.event_id, record.publish_topic, record.id,
                        )

                    db.add(record)

                await db.commit()

    finally:
        await close_kafka()
        logger.info("Outbox relay stopped")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)
    loop.run_until_complete(run())
