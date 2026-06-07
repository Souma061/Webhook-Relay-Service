import hashlib
import hmac
import ipaddress
import json
import logging
import uuid
from fastapi import APIRouter, Header, HTTPException, Request
from jsonschema import validate as jsonschema_validate, ValidationError as SchemaValidationError

from app.core.config import settings
from app.core.redis import get_redis
from app.core.database import async_session_factory
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.models.endpoint import Endpoint
from app.models.event import Event
from app.models.outbox import OutboxRecord

logger = logging.getLogger(__name__)

router = APIRouter()
rate_limiter = SlidingWindowRateLimiter()


async def _read_limited_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings.max_webhook_body_bytes:
            raise HTTPException(413, "request body too large")
    return bytes(body)


def _client_ip_allowed(request: Request, allowlist: list[str] | None) -> bool:
    if not allowlist:
        return True
    if not request.client or not request.client.host:
        return False

    try:
        client_ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False

    for entry in allowlist:
        try:
            if client_ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            logger.warning("Ignoring invalid endpoint IP allowlist entry: %s", entry)
    return False


@router.post("/hooks/{endpoint_id}", status_code=202)
async def receive_webhook(
    endpoint_id: str,
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    idempotency_key: str | None = Header(None, alias="x-idempotency-key"),
):
    raw_body = await _read_limited_body(request)

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
        if not _client_ip_allowed(request, endpoint.ip_allowlist):
            raise HTTPException(403, "sender ip is not allowed")

        if endpoint.rate_limit_rps is not None:
            rl_key = f"ingress:ep:{endpoint.id}"
            rpm = endpoint.rate_limit_rps * 60
            if not await rate_limiter.allow_request(rl_key, rpm):
                raise HTTPException(429, "endpoint rate limit exceeded")

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

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid JSON payload")

        if endpoint.request_body_schema:
            try:
                schema = json.loads(endpoint.request_body_schema)
                jsonschema_validate(payload, schema)
            except (json.JSONDecodeError, SchemaValidationError) as e:
                raise HTTPException(422, detail=str(e))

        if idempotency_key:
            redis = get_redis()
            dedup_key = f"idem:{endpoint.id}:{idempotency_key}"
            already_seen = await redis.set(dedup_key, "1", nx=True, ex=settings.idempotency_ttl_s)
            if not already_seen:
                return {"status": "duplicate", "event_id": None}

        _EXCLUDED_HEADERS = {"authorization", "cookie", "set-cookie"}
        request_headers = {
            key.lower(): value
            for key, value in request.headers.items()
            if key.lower() not in _EXCLUDED_HEADERS
        }

        event_id = uuid.uuid4()
        event = Event(
            id=event_id,
            endpoint_id=endpoint.id,
            idempotency_key=idempotency_key,
            request_body=payload,
            request_headers=request_headers,
            status="pending",
        )
        db.add(event)

        outbox = OutboxRecord(
            event_id=event_id,
            publish_key=str(endpoint.id),
            publish_topic=settings.kafka_topic_raw_events,
        )
        db.add(outbox)
        await db.commit()
        await db.refresh(event)

    return {"status": "accepted", "event_id": str(event.id)}
