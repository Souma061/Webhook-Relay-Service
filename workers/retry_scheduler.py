import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.kafka import init_kafka, get_kafka, close_kafka
from app.models.event import Event

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 15
RETRY_DELAY_S = 30


async def run():
    engine = create_async_engine(
        settings.database_url.replace("+asyncpg", ""),
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await init_kafka()

    while True:
        try:
            producer = get_kafka()
            if producer is None:
                logger.warning("Kafka not available, retrying in %ds", POLL_INTERVAL_S)
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            async with session_factory() as db:
                now = datetime.now(timezone.utc)
                stmt = (
                    select(Event)
                    .where(
                        Event.status == "failed",
                        Event.retry_at.is_not(None),
                        Event.retry_at <= now,
                        ~Event.is_discarded,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(50)
                )
                result = await db.execute(stmt)
                events = result.scalars().all()

                for ev in events:
                    try:
                        await producer.send_and_wait(
                            settings.kafka_topic_raw_events,
                            value={
                                "event_id": str(ev.id),
                                "endpoint_id": str(ev.endpoint_id),
                            },
                            key=str(ev.endpoint_id).encode(),
                        )
                        ev.status = "queued"
                        ev.retry_at = None
                        logger.info("Re-queued event %s", ev.id)
                    except Exception:
                        logger.exception("Failed to re-queue event %s", ev.id)
                        ev.retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_S)

                await db.commit()

        except Exception:
            logger.exception("Retry scheduler error")

        await asyncio.sleep(POLL_INTERVAL_S)


async def shutdown():
    await close_kafka()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        asyncio.run(shutdown())
