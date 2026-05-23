import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.kafka import get_kafka
from app.core.config import settings
from app.models.event import Event
from app.models.delivery_attempt import DeliveryAttempt
from app.schemas.api import EventOut, DeliveryAttemptOut

router = APIRouter(prefix="/api/events")


def _parse_uuid(value: str, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid {label} format")



@router.get("/", response_model=list[EventOut])
async def list_events(
    endpoint_id: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    async with async_session_factory() as db:
        stmt = select(Event).order_by(Event.received_at.desc()).limit(limit).offset(offset)
        if endpoint_id:
            stmt = stmt.where(Event.endpoint_id == _parse_uuid(endpoint_id, "endpoint_id"))
        result = await db.execute(stmt)
        return result.scalars().all()


@router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: str):
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await db.get(Event, ev_id)
        if not event:
            raise HTTPException(404, "event not found")
        return event


@router.get("/{event_id}/attempts", response_model=list[DeliveryAttemptOut])
async def get_event_attempts(event_id: str):
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        result = await db.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.event_id == ev_id)
            .order_by(DeliveryAttempt.attempt_number)
        )
        return result.scalars().all()


@router.post("/{event_id}/replay", status_code=202)
async def replay_event(event_id: str):
    """Re-publish the stored event to the raw-events Kafka topic for full re-processing.

    Useful for retrying DLQ events or any failed delivery after fixing a config issue.
    The event goes through the full transform → delivery pipeline again.
    """
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await db.get(Event, ev_id)
        if not event:
            raise HTTPException(404, "event not found")

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
"""
events.py — Event and delivery attempt API.

Provides read-only access to the webhook audit trail:
- GET /api/events — list events (paginated, optional endpoint filter)
- GET /api/events/{id} — get a single event with payload
- GET /api/events/{id}/attempts — get all delivery attempts for an event
- POST /api/events/{id}/replay — re-inject event into the raw-events Kafka topic (Phase 2)
"""

