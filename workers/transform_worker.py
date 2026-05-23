"""
transform_worker.py — Phase 2 Transform Consumer.

Reads raw-events from Kafka, loads route configs from PostgreSQL,
applies the transform pipeline to each route, and produces one
transformed-events message per route for the delivery worker.

Consumer group: relay-transform-group
  Input topic:  raw-events
  Output topic: transformed-events

Run standalone:
    python -m workers.transform_worker
"""
import asyncio
import json
import logging
import os
import signal

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import select

# ── Bootstrap settings & DB before importing app modules ──────────────────────
os.environ.setdefault("RELAY_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/webhook_relay")

from app.core.config import settings  # noqa: E402
from app.core.database import async_session_factory, init_db  # noqa: E402
import app.models  # noqa: E402 — registers all ORM models so relationships resolve
from app.models.event import Event  # noqa: E402
from app.models.route import Route  # noqa: E402
from app.transform.engine import apply_pipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [transform-worker] %(message)s")
logger = logging.getLogger(__name__)

_stop = asyncio.Event()


def _handle_signal(*_):
    logger.info("Shutdown signal received — stopping consumer loop")
    _stop.set()


async def run():
    """Main consumer loop."""
    await init_db()

    consumer = AIOKafkaConsumer(
        settings.kafka_topic_raw_events,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_transform,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
    )

    await consumer.start()
    await producer.start()
    logger.info(
        "Transform worker started (topic=%s, group=%s)",
        settings.kafka_topic_raw_events,
        settings.kafka_consumer_group_transform,
    )

    try:
        async for msg in consumer:
            if _stop.is_set():
                break

            data = msg.value
            event_id = data.get("event_id")
            endpoint_id = data.get("endpoint_id")

            if not event_id or not endpoint_id:
                logger.warning("Skipping malformed message: %s", data)
                continue

            try:
                await _process(producer, event_id, endpoint_id)
            except Exception as exc:
                logger.error("Error processing event %s: %s", event_id, exc, exc_info=True)
                # Do NOT re-raise — we commit the offset and move on so one
                # bad event can't block the whole consumer group.
    finally:
        logger.info("Stopping transform worker …")
        await consumer.stop()
        await producer.stop()


async def _process(producer: AIOKafkaProducer, event_id: str, endpoint_id: str):
    """Load routes from DB, apply transforms, publish to transformed-events."""
    async with async_session_factory() as db:
        import uuid as _uuid

        ev = await db.get(Event, _uuid.UUID(event_id))
        if not ev:
            logger.warning("Event %s not found in DB — skipping", event_id)
            return

        result = await db.execute(
            select(Route).where(
                Route.endpoint_id == _uuid.UUID(endpoint_id),
                Route.is_active == True,
            )
        )
        routes = result.scalars().all()

    if not routes:
        logger.info("Event %s has no active routes — nothing to deliver", event_id)
        return

    for route in routes:
        body = ev.request_body

        # Apply transform pipeline if configured on this route
        if route.transform_pipeline:
            try:
                body = apply_pipeline(route.transform_pipeline, body)
            except Exception as exc:
                logger.error(
                    "Transform failed for route %s on event %s: %s",
                    route.id, event_id, exc,
                )
                # Publish with original body so delivery still gets attempted
                body = ev.request_body

        await producer.send_and_wait(
            settings.kafka_topic_transformed_events,
            value={
                "event_id": event_id,
                "route_id": str(route.id),
                "url": route.url,
                "method": route.method,
                "headers": route.headers or {},
                "body": body,
                "timeout_ms": route.timeout_ms,
                "max_retries": route.max_retries,
                "retry_backoff_ms": route.retry_backoff_ms,
            },
            key=str(route.id).encode(),  # partition by route for ordering
        )
        logger.info(
            "Event %s → route %s → transformed-events",
            event_id, route.id,
        )


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)
    loop.run_until_complete(run())
