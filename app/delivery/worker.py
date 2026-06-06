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


async def schedule_deliveries(event, routes: list[dict]):
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


async def _deliver_with_retry(
    event_id, route: dict, body: dict, attempt: int, rate_limit_waits: int = 0
):
    import httpx
    import asyncio
    import random

    MAX_RATE_LIMIT_WAITS = 5

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

    route_rpm = route.get("rate_limit_rpm")
    if route_rpm is not None:
        if not await rate_limiter.allow_request(f"route:{route['id']}", route_rpm):
            async with async_session_factory() as db:
                db.add(DeliveryAttempt(
                    event_id=event_id, route_id=route["id"], attempt_number=attempt,
                    request_url=url, request_body=body, response_status=None,
                    response_body=None, error="route_rate_limited", duration_ms=0,
                ))
                await db.commit()
            return

    if not await rate_limiter.allow_request(f"dest:{url}"):
        if rate_limit_waits >= MAX_RATE_LIMIT_WAITS:
            async with async_session_factory() as db:
                db.add(DeliveryAttempt(
                    event_id=event_id, route_id=route["id"], attempt_number=attempt,
                    request_url=url, request_body=body, response_status=None,
                    response_body=None, error="rate_limited_abandoned", duration_ms=0,
                ))
                await db.commit()
            return
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

