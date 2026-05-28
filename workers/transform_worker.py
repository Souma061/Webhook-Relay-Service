from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import select

os.environ.setdefault(
    "RELAY_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/webhook_relay",
)

from app.core.config import settings
from app.core.database import async_session_factory, init_db
import app.models
from app.models.event import Event
from app.models.route import Route
from app.transform.engine import apply_pipeline
from app.transform.filter import route_matches_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [transform-worker] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_stop = asyncio.Event()


def _handle_signal(*_) -> None:
    logger.info("Shutdown signal received — stopping consumer loop")
    _stop.set()


async def run() -> None:
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
        "Transform worker started [topic=%s group=%s]",
        settings.kafka_topic_raw_events,
        settings.kafka_consumer_group_transform,
    )

    try:
        async for msg in consumer:
            if _stop.is_set():
                break
            await _handle_message(producer, msg.value)
    finally:
        logger.info("Stopping transform worker …")
        await consumer.stop()
        await producer.stop()


async def _handle_message(producer: AIOKafkaProducer, data: dict) -> None:
    event_id = data.get("event_id")
    endpoint_id = data.get("endpoint_id")

    if not event_id or not endpoint_id:
        logger.warning("Skipping malformed message (missing event_id or endpoint_id): %s", data)
        return

    try:
        await _process_event(producer, event_id, endpoint_id)
    except Exception:
        logger.exception("Unhandled error while processing event %s — offset committed, moving on", event_id)


async def _process_event(
    producer: AIOKafkaProducer,
    event_id: str,
    endpoint_id: str,
) -> None:
    async with async_session_factory() as db:
        event = await db.get(Event, uuid.UUID(event_id))
        if not event:
            logger.warning("Event %s not found in DB — skipping", event_id)
            return

        result = await db.execute(
            select(Route).where(
                Route.endpoint_id == uuid.UUID(endpoint_id),
                Route.is_active == True,
            )
        )
        routes = result.scalars().all()

    if not routes:
        logger.info("Event %s has no active routes — nothing to deliver", event_id)
        return

    logger.info("Processing event %s across %d active route(s)", event_id, len(routes))

    for route in routes:
        await _dispatch_route(producer, event, route)


async def _dispatch_route(
    producer: AIOKafkaProducer,
    event: Event,
    route: Route,
) -> None:
    event_id = str(event.id)
    route_id = str(route.id)

    should_deliver = route_matches_event(
        filter_expression=route.filter_expression,
        body=event.request_body,
        headers=event.request_headers,
        route_id=route_id,
        event_id=event_id,
    )

    if not should_deliver:
        return

    body = event.request_body
    if route.transform_pipeline:
        try:
            body = apply_pipeline(route.transform_pipeline, body)
        except Exception:
            logger.exception(
                "Transform pipeline failed for route %s on event %s — "
                "skipping delivery",
                route_id, event_id,
            )
            return

    await producer.send_and_wait(
        settings.kafka_topic_transformed_events,
        value={
            "event_id":        event_id,
            "route_id":        route_id,
            "url":             route.url,
            "method":          route.method,
            "headers":         route.headers or {},
            "body":            body,
            "timeout_ms":      route.timeout_ms,
            "max_retries":     route.max_retries,
            "retry_backoff_ms": route.retry_backoff_ms,
        },
        key=route_id.encode(),
    )
    logger.info("Event %s → route %s dispatched to transformed-events", event_id, route_id)


# ── Entry point ────────────────────────────────────────────────────────────────

async def _process(producer: AIOKafkaProducer, event_id: str, endpoint_id: str) -> None:
    await _process_event(producer, event_id, endpoint_id)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)
    loop.run_until_complete(run())
