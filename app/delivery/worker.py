import asyncio
import json
import uuid
from datetime import datetime, timezone

from app.core.database import async_session_factory
from app.core.config import settings
from app.models.delivery_attempt import DeliveryAttempt
from app.transform.engine import apply_pipeline
from app.delivery.circuit_breaker import CircuitBreaker
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.core.url_security import validate_delivery_url_for_request

rate_limiter = SlidingWindowRateLimiter()


# async def schedule_deliveries(event, routes: list[dict]):
#     for route in routes:
#         body = event.request_body
#         transform_pipeline = route.get("transform_pipeline")
#         if transform_pipeline:
#             body = apply_pipeline(transform_pipeline, body)

#         await _deliver_with_retry(
#             event_id=event.id,
#             route=route,
#             body=body,
#             attempt=0,
#         )

async def schedule_deliveries(event,routes: list[dict]):
    tasks = []
    for route in routes:
        body = event.request_body
        transform_pipeline = route.get("transform_pipeline")
        if transform_pipeline:
            body = apply_pipeline(transform_pipeline, body)

        tasks.append(_deliver_with_retry(
            event_id=event.id,
            route=route,
            body=body,
            attempt=0,
        ))

    await asyncio.gather(*tasks, return_exceptions=True)
    # Note: return_exceptions=True prevents one failing delivery from cancelling the others. In Phase 2, we may want more robust error handling/logging here.


async def _deliver_with_retry(
    event_id, route: dict, body: dict, attempt: int, rate_limit_waits: int = 0
):
    import httpx
    import asyncio
    import random

    MAX_RATE_LIMIT_WAITS = 5  # safety cap: give up after 5 consecutive rate-limit waits

    url = route["url"]
    method = route.get("method", "POST")
    headers = route.get("headers") or {}
    timeout_ms = route.get("timeout_ms", settings.delivery_timeout_ms)
    max_retries = route.get("max_retries", settings.max_delivery_attempts)
    backoff_ms = route.get("retry_backoff_ms", settings.retry_backoff_ms)

    cb = CircuitBreaker(url)
    if await cb.is_open():
        async with async_session_factory() as db:
            db.add(DeliveryAttempt(
                event_id=event_id, route_id=route["id"], attempt_number=attempt,
                request_url=url, request_body=body, response_status=None,
                response_body=None, error="circuit_breaker_open", duration_ms=0,
            ))
            await db.commit()
        next_attempt = attempt + 1
        if next_attempt < max_retries:
            await asyncio.sleep(settings.circuit_breaker_cooldown_s)
            await _deliver_with_retry(event_id, route, body, next_attempt)
        return

    if not await rate_limiter.allow_request(url):
        if rate_limit_waits >= MAX_RATE_LIMIT_WAITS:
            # Destination is persistently over-limit — log and abandon
            async with async_session_factory() as db:
                db.add(DeliveryAttempt(
                    event_id=event_id, route_id=route["id"], attempt_number=attempt,
                    request_url=url, request_body=body, response_status=None,
                    response_body=None, error="rate_limited_abandoned", duration_ms=0,
                ))
                await db.commit()
            return
        # Wait 1 s then retry the same attempt (not a delivery failure, so attempt stays)
        await asyncio.sleep(1)
        await _deliver_with_retry(event_id, route, body, attempt, rate_limit_waits + 1)
        return

    start = datetime.now(timezone.utc)
    error = None
    response_status = None
    response_body = None

    try:
        await asyncio.to_thread(validate_delivery_url_for_request, url)
        async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
            resp = await client.request(
                method=method,
                url=url,
                json=body,
                headers=headers,
            )
        response_status = resp.status_code
        response_body = resp.text
    except httpx.TimeoutException as e:
        error = f"timeout: {e}"
    except httpx.ConnectError as e:
        error = f"connection_error: {e}"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

    async with async_session_factory() as db:
        db.add(DeliveryAttempt(
            event_id=event_id,
            route_id=route["id"],
            attempt_number=attempt,
            request_url=url,
            request_body=body,
            response_status=response_status,
            response_body=response_body,
            error=error,
            duration_ms=duration_ms,
        ))
        await db.commit()

    is_success = response_status and 200 <= response_status < 300
    is_client_error = response_status and 400 <= response_status < 500

    if is_success:
        await cb.record_success()
        return

    await cb.record_failure()

    if is_client_error:
        return

    if error or (response_status and response_status >= 500):
        next_attempt = attempt + 1
        if next_attempt < max_retries:
            delay = (backoff_ms * (2 ** attempt)) / 1000
            delay = delay + random.uniform(0, delay * 0.5)
            await asyncio.sleep(delay)
            await _deliver_with_retry(event_id, route, body, next_attempt)
        else:
            # ── All retries exhausted — publish to Dead Letter Queue ──────────
            # The DLQ consumer (or an operator) can replay this via
            # POST /api/events/{event_id}/replay later.
            try:
                from app.core.kafka import get_kafka
                producer = get_kafka()
                if producer is not None:
                    await producer.send_and_wait(
                        settings.kafka_topic_dead_letter,
                        value={
                            "event_id": str(event_id),
                            "route_id": str(route["id"]),
                            "url": url,
                            "body": body,
                            "last_error": error,
                            "last_response_status": response_status,
                            "attempts": max_retries,
                        },
                        key=str(event_id).encode(),
                    )
            except RuntimeError:
                pass
        return
"""
worker.py — Delivery worker with retry logic.

Runs as a Kafka consumer task in Phase 2 (delivery_worker.py calls _deliver_with_retry directly).

Flow:
1. Apply transform pipeline on the event payload
2. Make HTTP request to the destination URL
3. Log the delivery attempt (success or failure) to PostgreSQL
4. On success: done
5. On 4xx error: abandon (bad config, retrying won't help)
6. On 5xx/network error: retry with exponential backoff + jitter
7. After exhausting retries: publish to Kafka dead-letter topic for manual replay

The backoff uses: base_ms × 2^attempt + random jitter (0-50%)
Jitter prevents the thundering herd problem when many events fail at once.
"""
