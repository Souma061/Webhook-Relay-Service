import asyncio
import json
import logging
import os
import signal
import uuid

from aiokafka import AIOKafkaConsumer

os.environ.setdefault("RELAY_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/webhook_relay")
os.environ.setdefault("RELAY_REDIS_URL", "redis://localhost:6379/0")

from app.core.config import settings
from app.core.database import init_db
from app.core.redis import init_redis, close_redis
import app.models
from app.delivery.worker import _deliver_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [delivery-worker] %(message)s")
logger = logging.getLogger(__name__)

_stop = asyncio.Event()

_GLOBAL_SEMAPHORE = asyncio.Semaphore(50)


def _handle_signal(*_):
    logger.info("Shutdown signal received — stopping consumer loop")
    _stop.set()


async def run():
    await init_db()
    await init_redis()

    consumer = AIOKafkaConsumer(
        settings.kafka_topic_transformed_events,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_delivery,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )

    await consumer.start()
    logger.info(
        "Delivery worker started (topic=%s, group=%s)",
        settings.kafka_topic_transformed_events,
        settings.kafka_consumer_group_delivery,
    )

    pending: set[asyncio.Task] = set()

    try:
        async for msg in consumer:
            if _stop.is_set():
                break

            data = msg.value
            if not data.get("event_id") or not data.get("route_id") or not data.get("url"):
                logger.warning("Skipping malformed delivery message: %s", data)
                continue

            task = asyncio.create_task(_dispatch(data, consumer))
            pending.add(task)
            task.add_done_callback(pending.discard)

        if pending:
            logger.info("Waiting for %d in-flight deliveries to finish…", len(pending))
            await asyncio.gather(*pending, return_exceptions=True)

    finally:
        logger.info("Stopping delivery worker …")
        await consumer.stop()
        await close_redis()


async def _dispatch(data: dict, consumer: AIOKafkaConsumer):
    async with _GLOBAL_SEMAPHORE:
        try:
            route = {
                "id": uuid.UUID(data["route_id"]),
                "url": data["url"],
                "method": data.get("method", "POST"),
                "headers": data.get("headers") or {},
                "timeout_ms": data.get("timeout_ms", settings.delivery_timeout_ms),
                "max_retries": data.get("max_retries", settings.max_delivery_attempts),
                "retry_backoff_ms": data.get("retry_backoff_ms", settings.retry_backoff_ms),
            }
            await _deliver_with_retry(
                event_id=uuid.UUID(data["event_id"]),
                route=route,
                body=data.get("body", {}),
                attempt=0,
            )
        except Exception as exc:
            logger.error(
                "Unhandled error delivering event %s to %s: %s",
                data.get("event_id"), data.get("url"), exc, exc_info=True,
            )
        else:
            try:
                await consumer.commit()
            except Exception:
                logger.exception("Failed to commit offset for event %s", data.get("event_id"))


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)
    loop.run_until_complete(run())
