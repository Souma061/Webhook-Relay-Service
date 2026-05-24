"""
events.py — Event audit trail and Dead Letter Queue management API.

Routes mounted under /api:
  GET  /api/events                     Paginated event list (filter by endpoint)
  GET  /api/events/{id}                Single event detail
  GET  /api/events/{id}/attempts       All delivery attempts for an event
  POST /api/events/{id}/replay         Re-inject event into raw-events topic

  GET  /api/dlq                        DLQ: events with failed deliveries
  POST /api/dlq/{id}/discard           DLQ: soft-delete (hide from queue)
  POST /api/dlq/{id}/restore           DLQ: undo a discard
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.core.kafka import get_kafka
from app.core.config import settings
from app.models.event import Event
from app.models.delivery_attempt import DeliveryAttempt
from app.schemas.api import DeliveryAttemptOut, DlqEventOut, EventOut

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_uuid(value: str, label: str = "id") -> uuid.UUID:
    """Parse a UUID string, raising HTTP 400 on invalid format."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid {label} format")


async def _get_event_or_404(db, event_id: uuid.UUID) -> Event:
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    return event


# ── Events ─────────────────────────────────────────────────────────────────────

events_router = APIRouter(prefix="/api/events", tags=["Events"])


@events_router.get("/", response_model=list[EventOut])
async def list_events(
    endpoint_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """List events, newest first. Optionally filter by endpoint_id."""
    async with async_session_factory() as db:
        stmt = (
            select(Event)
            .order_by(Event.received_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if endpoint_id:
            stmt = stmt.where(Event.endpoint_id == _parse_uuid(endpoint_id, "endpoint_id"))
        result = await db.execute(stmt)
        return result.scalars().all()


@events_router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: str):
    """Get a single event by ID."""
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        return await _get_event_or_404(db, ev_id)


@events_router.get("/{event_id}/attempts", response_model=list[DeliveryAttemptOut])
async def get_event_attempts(event_id: str):
    """Get all delivery attempts for an event, ordered by attempt number."""
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        result = await db.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.event_id == ev_id)
            .order_by(DeliveryAttempt.attempt_number)
        )
        return result.scalars().all()


@events_router.post("/{event_id}/replay", status_code=202)
async def replay_event(event_id: str):
    """
    Re-publish the stored event to raw-events for full re-processing.
    Works for any event — not just DLQ events. The transform → delivery
    pipeline will run again from scratch.
    """
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await _get_event_or_404(db, ev_id)

    producer = get_kafka()
    await producer.send_and_wait(
        settings.kafka_topic_raw_events,
        value={
            "event_id": str(event.id),
            "endpoint_id": str(event.endpoint_id),
            "is_replay": True,
        },
        key=str(event.endpoint_id).encode(),
    )
    return {"status": "replaying", "event_id": str(event.id)}


# ── Dead Letter Queue ──────────────────────────────────────────────────────────

dlq_router = APIRouter(prefix="/api/dlq", tags=["Dead Letter Queue"])


@dlq_router.get("/", response_model=list[DlqEventOut])
async def list_dlq(
    include_discarded: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """
    List events that have at least one failed delivery attempt.
    By default, discarded events are hidden. Pass include_discarded=true to show all.

    Returns events with their last delivery attempt's error, URL, and status.
    """
    async with async_session_factory() as db:
        # Subquery: for each event, get the last delivery attempt with an error
        # A "DLQ event" = any event with a non-null error on its final attempt,
        # OR a 5xx response on its latest attempt.
        last_attempt_subq = (
            select(
                DeliveryAttempt.event_id,
                func.max(DeliveryAttempt.attempt_number).label("max_attempt"),
            )
            .where(
                (DeliveryAttempt.error.is_not(None))
                | (DeliveryAttempt.response_status >= 500)
            )
            .group_by(DeliveryAttempt.event_id)
            .subquery()
        )

        # Join to get the actual last attempt row
        last_attempt = (
            select(DeliveryAttempt)
            .join(
                last_attempt_subq,
                (DeliveryAttempt.event_id == last_attempt_subq.c.event_id)
                & (DeliveryAttempt.attempt_number == last_attempt_subq.c.max_attempt),
            )
            .subquery()
        )

        # Count total attempts per event
        attempt_counts = (
            select(
                DeliveryAttempt.event_id,
                func.count(DeliveryAttempt.id).label("total"),
            )
            .group_by(DeliveryAttempt.event_id)
            .subquery()
        )

        # Main query: join events with the last failing attempt
        stmt = (
            select(
                Event.id,
                Event.endpoint_id,
                Event.received_at,
                Event.request_body,
                Event.is_discarded,
                Event.discarded_at,
                last_attempt.c.error.label("last_error"),
                last_attempt.c.response_status.label("last_status"),
                last_attempt.c.request_url.label("last_url"),
                attempt_counts.c.total.label("total_attempts"),
            )
            .join(last_attempt, Event.id == last_attempt.c.event_id)
            .join(attempt_counts, Event.id == attempt_counts.c.event_id)
            .order_by(Event.received_at.desc())
            .limit(limit)
            .offset(offset)
        )

        if not include_discarded:
            stmt = stmt.where(Event.is_discarded == False)  # noqa: E712

        rows = (await db.execute(stmt)).mappings().all()

    return [
        DlqEventOut(
            event_id=row["id"],
            endpoint_id=row["endpoint_id"],
            received_at=row["received_at"],
            request_body=row["request_body"],
            is_discarded=row["is_discarded"],
            discarded_at=row["discarded_at"],
            last_error=row["last_error"],
            last_status=row["last_status"],
            last_url=row["last_url"],
            total_attempts=row["total_attempts"],
        )
        for row in rows
    ]


@dlq_router.post("/{event_id}/discard", status_code=200)
async def discard_dlq_event(event_id: str):
    """
    Soft-delete a DLQ event — hide it from the default DLQ view.
    The event and its delivery attempts remain in the database; this is
    purely an operator acknowledgment ("I've seen this, don't show it again").
    """
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await _get_event_or_404(db, ev_id)
        if event.is_discarded:
            return {"status": "already_discarded", "event_id": str(ev_id)}
        event.is_discarded = True
        event.discarded_at = datetime.now(timezone.utc)
        await db.commit()
    return {"status": "discarded", "event_id": str(ev_id)}


@dlq_router.post("/{event_id}/restore", status_code=200)
async def restore_dlq_event(event_id: str):
    """
    Undo a discard — bring a discarded event back into the active DLQ view.
    """
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await _get_event_or_404(db, ev_id)
        event.is_discarded = False
        event.discarded_at = None
        await db.commit()
    return {"status": "restored", "event_id": str(ev_id)}


# Export both routers so main.py can mount them
router = events_router
