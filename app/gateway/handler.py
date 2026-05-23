import hashlib
import hmac
import json
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import settings
from app.core.redis import get_redis
from app.core.kafka import get_kafka
from app.core.database import async_session_factory
from app.models.endpoint import Endpoint
from app.models.event import Event

router = APIRouter()


@router.post("/hooks/{endpoint_id}", status_code=202)
async def receive_webhook(
    endpoint_id: str,
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    idempotency_key: str | None = Header(None),
):
    raw_body = await request.body()
    payload = await request.json()

    async with async_session_factory() as db:
        try:
            ep_id = uuid.UUID(endpoint_id)
        except ValueError:
            raise HTTPException(400, "invalid endpoint id")

        endpoint = await db.get(Endpoint, ep_id)
        if not endpoint:
            raise HTTPException(404, "endpoint not found")
        if not endpoint.is_active:
            raise HTTPException(410, "endpoint is disabled")

        if endpoint.hmac_secret:
            if not x_hub_signature_256:
                raise HTTPException(401, "missing signature header")

            prefix = "sha256="
            if not x_hub_signature_256.startswith(prefix):
                raise HTTPException(401, "invalid signature format")

            provided = x_hub_signature_256[len(prefix):]
            computed = hmac.new(
                endpoint.hmac_secret.encode(),
                raw_body,
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(provided, computed):
                raise HTTPException(401, "invalid signature")

        if idempotency_key:
            redis = get_redis()
            dedup_key = f"idem:{endpoint.id}:{idempotency_key}"
            already_seen = await redis.set(dedup_key, "1", nx=True, ex=settings.idempotency_ttl_s)# this will return True if the key was set (i.e., not seen before), or False if it already exists
            if not already_seen:
                return {"status": "duplicate", "event_id": None}

        event = Event(
            endpoint_id=endpoint.id,
            idempotency_key=idempotency_key,
            request_body=payload,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)

    # ── Phase 2: publish a lightweight message to Kafka ────────────────────────
    # The transform worker will pick this up, load the routes from DB,
    # apply transforms, and publish one message per route to transformed-events.
    producer = get_kafka()
    await producer.send_and_wait(
        settings.kafka_topic_raw_events,
        value={
            "event_id": str(event.id),
            "endpoint_id": str(event.endpoint_id),
        },
        key=str(event.endpoint_id).encode(),  # partition by endpoint for ordering
    )

    return {"status": "accepted", "event_id": str(event.id)}
"""
handler.py — Webhook ingestion endpoint.

Accepts POST requests at /hooks/{endpoint_id} and:
1. Looks up the endpoint config from PostgreSQL
2. Verifies HMAC-SHA256 signature if the endpoint has a secret
3. Checks idempotency key via Redis (prevents duplicate processing)
4. Stores the raw event in the events table
5. Publishes a light message (event_id, endpoint_id) to Kafka raw-events topic
6. Returns 202 Accepted immediately

Phase 2: delivery is fully decoupled — transform + delivery run in separate worker processes.
"""
