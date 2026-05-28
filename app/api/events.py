from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.kafka import get_kafka
from app.middleware.rbac import get_workspace_membership, require_workspace_role
from app.models.delivery_attempt import DeliveryAttempt
from app.models.endpoint import Endpoint
from app.models.event import Event
from app.models.workspace import WorkspaceMembership
from app.schemas.api import DeliveryAttemptOut, DlqEventOut, EventOut

router = APIRouter(prefix="/api/workspaces/{workspace_id}")


def _parse_uuid(value: str, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid {label} format")


async def _get_event_or_404(db, event_id: uuid.UUID, ws_id: uuid.UUID) -> Event:
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    ep = await db.get(Endpoint, event.endpoint_id)
    if not ep or ep.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="event not found")
    return event


async def _list_workspace_endpoint_ids(db, ws_id: uuid.UUID) -> list[uuid.UUID]:
    result = await db.execute(
        select(Endpoint.id).where(Endpoint.workspace_id == ws_id)
    )
    return result.scalars().all()


@router.get("/events", response_model=list[EventOut])
async def list_events(
    endpoint_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    async with async_session_factory() as db:
        ws_ep_ids = await _list_workspace_endpoint_ids(db, membership.workspace_id)
        if not ws_ep_ids:
            return []

        stmt = (
            select(Event)
            .where(Event.endpoint_id.in_(ws_ep_ids))
            .order_by(Event.received_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if endpoint_id:
            ep_id = _parse_uuid(endpoint_id, "endpoint_id")
            if ep_id not in ws_ep_ids:
                raise HTTPException(404, "endpoint not found in this workspace")
            stmt = stmt.where(Event.endpoint_id == ep_id)
        result = await db.execute(stmt)
        return result.scalars().all()


@router.get("/events/{event_id}", response_model=EventOut)
async def get_event(
    event_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        return await _get_event_or_404(db, ev_id, membership.workspace_id)


@router.get("/events/{event_id}/attempts", response_model=list[DeliveryAttemptOut])
async def get_event_attempts(
    event_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        await _get_event_or_404(db, ev_id, membership.workspace_id)
        result = await db.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.event_id == ev_id)
            .order_by(DeliveryAttempt.attempt_number)
        )
        return result.scalars().all()


@router.post("/events/{event_id}/replay", status_code=202)
async def replay_event(
    event_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await _get_event_or_404(db, ev_id, membership.workspace_id)

    producer = get_kafka()
    if producer is None:
        raise HTTPException(503, "Kafka is unavailable — cannot replay event")
    try:
        await producer.send_and_wait(
            settings.kafka_topic_raw_events,
            value={
                "event_id": str(event.id),
                "endpoint_id": str(event.endpoint_id),
                "is_replay": True,
            },
            key=str(event.endpoint_id).encode(),
        )
    except Exception:
        raise HTTPException(503, "Failed to publish replay message to Kafka")
    return {"status": "replaying", "event_id": str(event.id)}


@router.get("/dlq", response_model=list[DlqEventOut])
async def list_dlq(
    include_discarded: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    async with async_session_factory() as db:
        ws_ep_ids = await _list_workspace_endpoint_ids(db, membership.workspace_id)
        if not ws_ep_ids:
            return []

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

        last_attempt = (
            select(DeliveryAttempt)
            .join(
                last_attempt_subq,
                (DeliveryAttempt.event_id == last_attempt_subq.c.event_id)
                & (DeliveryAttempt.attempt_number == last_attempt_subq.c.max_attempt),
            )
            .subquery()
        )

        attempt_counts = (
            select(
                DeliveryAttempt.event_id,
                func.count(DeliveryAttempt.id).label("total"),
            )
            .group_by(DeliveryAttempt.event_id)
            .subquery()
        )

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
            .where(Event.endpoint_id.in_(ws_ep_ids))
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


@router.post("/dlq/{event_id}/discard", status_code=200)
async def discard_dlq_event(
    event_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await _get_event_or_404(db, ev_id, membership.workspace_id)
        if event.is_discarded:
            return {"status": "already_discarded", "event_id": str(ev_id)}
        event.is_discarded = True
        event.discarded_at = datetime.now(timezone.utc)
        await db.commit()
    return {"status": "discarded", "event_id": str(ev_id)}


@router.post("/dlq/{event_id}/restore", status_code=200)
async def restore_dlq_event(
    event_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    ev_id = _parse_uuid(event_id, "event_id")
    async with async_session_factory() as db:
        event = await _get_event_or_404(db, ev_id, membership.workspace_id)
        event.is_discarded = False
        event.discarded_at = None
        await db.commit()
    return {"status": "restored", "event_id": str(ev_id)}
