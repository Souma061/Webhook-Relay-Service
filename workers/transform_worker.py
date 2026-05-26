"""
transform_worker.py — Phase 2/3 Transform & Filter Consumer.

Reads raw-events from Kafka, loads route configs from PostgreSQL, evaluates
route filter expressions, applies the transform pipeline, and produces one
transformed-events message per matching route for the delivery worker.

Pipeline for each event:
  1. Fetch the full Event record from DB (body + headers).
  2. Fetch all active Routes for the endpoint.
  3. For each route:
     a. Evaluate filter_expression against {body, headers} context.
        → No match  : skip route, log it, move on.
        → Match / no filter: continue to step b.
     b. Apply transform_pipeline to the body (if configured).
     c. Publish one message to `transformed-events` topic.

Consumer group : relay-transform-group
Input topic    : raw-events
Output topic   : transformed-events

Run standalone:
    python -m workers.transform_worker
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import select

# ── Bootstrap settings & DB before importing app modules ──────────────────────
os.environ.setdefault(
    "RELAY_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/webhook_relay",
)

from app.core.config import settings                        # noqa: E402
from app.core.database import async_session_factory, init_db  # noqa: E402
import app.models                                           # noqa: E402  registers all ORM models
from app.models.event import Event                          # noqa: E402
from app.models.route import Route                          # noqa: E402
from app.transform.engine import apply_pipeline             # noqa: E402
from app.transform.filter import route_matches_event        # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [transform-worker] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_stop = asyncio.Event()


# ── Signal handling ────────────────────────────────────────────────────────────

def _handle_signal(*_) -> None:
    logger.info("Shutdown signal received — stopping consumer loop")
    _stop.set()


# ── Main consumer loop ─────────────────────────────────────────────────────────

async def run() -> None:
    """Start the consumer loop. Runs until a SIGINT/SIGTERM is received."""
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


# ── Message dispatch ───────────────────────────────────────────────────────────

async def _handle_message(producer: AIOKafkaProducer, data: dict) -> None:
    """
    Entry point for each Kafka message consumed from `raw-events`.

    Validates the message shape and delegates to _process_event().
    Errors are caught here so a single bad message never blocks the consumer.
    """
    event_id = data.get("event_id")
    endpoint_id = data.get("endpoint_id")

    if not event_id or not endpoint_id:
        logger.warning("Skipping malformed message (missing event_id or endpoint_id): %s", data)
        return

    try:
        await _process_event(producer, event_id, endpoint_id)
    except Exception:
        logger.exception("Unhandled error while processing event %s — offset committed, moving on", event_id)


# ── Core processing logic ──────────────────────────────────────────────────────

async def _process_event(
    producer: AIOKafkaProducer,
    event_id: str,
    endpoint_id: str,
) -> None:
    """
    Load the Event and its Routes from the database, then fan-out:

    For every active Route:
      1. Evaluate the route's filter_expression (if any) against the event.
      2. If the filter matches (or no filter is set), apply the transform pipeline.
      3. Publish one message to `transformed-events` for the delivery worker.
    """
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
    """
    Evaluate filter, apply transform, and publish a delivery task for one route.

    Args:
        producer: AIOKafkaProducer to publish the transformed message.
        event:    The Event ORM record (contains body + headers).
        route:    The Route ORM record (contains filter + transform config).
    """
    event_id = str(event.id)
    route_id = str(route.id)

    # ── Step 1: Evaluate the filter expression ─────────────────────────────────
    # route_matches_event() uses a unified {body, headers} context so filters
    # can target any part of the incoming request (payload field OR HTTP header).
    should_deliver = route_matches_event(
        filter_expression=route.filter_expression,
        body=event.request_body,
        headers=event.request_headers,
        route_id=route_id,
        event_id=event_id,
    )

    if not should_deliver:
        # Logging is already handled inside route_matches_event().
        return

    # ── Step 2: Apply the transform pipeline (if configured) ──────────────────
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

    # ── Step 3: Publish to `transformed-events` for the delivery worker ────────
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
        key=route_id.encode(),  # Partition by route for ordering guarantees
    )
    logger.info("Event %s → route %s dispatched to transformed-events", event_id, route_id)


# ── Entry point ────────────────────────────────────────────────────────────────

async def _process(producer: AIOKafkaProducer, event_id: str, endpoint_id: str) -> None:
    """Compatibility wrapper for test imports.

    Calls the internal `_process_event` implementation.
    """
    await _process_event(producer, event_id, endpoint_id)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)
    loop.run_until_complete(run())
