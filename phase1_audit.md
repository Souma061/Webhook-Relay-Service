# Phase 1 Implementation Audit — Webhook Relay Service

## PRD Checklist vs. Actual Implementation

### Phase 1 Goals (from PRD §9)
> Working ingestion → transform → delivery loop with PostgreSQL, no Kafka.

---

## What's Done & Solid

| PRD Item | Status | File(s) |
|---|---|---|
| FastAPI project structure, config, health endpoint | ✅ Complete | `app/main.py`, `app/core/config.py` |
| PostgreSQL models (endpoints, routes, events, delivery_attempts) | ✅ Complete | `app/models/*.py` |
| Auto-table creation on startup | ✅ Complete | `app/core/database.py` → `init_db()` |
| CRUD API for endpoints and routes | ✅ Partial | `app/api/endpoints.py` |
| Webhook ingestion handler (`POST /hooks/{id}`) | ✅ Complete | `app/gateway/handler.py` |
| HMAC signature verification | ✅ Complete | `app/gateway/handler.py` L40-56 |
| Idempotency key dedup (Redis) | ✅ Complete | `app/gateway/handler.py` L58-63 |
| Basic transform engine (template strings + pass-through + JMESPath) | ✅ Complete | `app/transform/engine.py` |
| Async delivery worker | ✅ Complete | `app/delivery/worker.py` |
| Exponential backoff with jitter | ✅ Complete | `app/delivery/worker.py` L87-88 |
| Admin API: list events + delivery attempts | ✅ Complete | `app/api/events.py` |
| Circuit breaker per destination (Redis-backed) | ✅ Exists (Phase 3 feature — implemented early!) | `app/delivery/circuit_breaker.py` |
| Sliding window rate limiter | ✅ Exists (Phase 2 feature — implemented early!) | `app/core/rate_limiter.py` |
| Docker Compose (app + postgres + redis) | ✅ Complete | `docker-compose.yml` |
| `.env.example` | ✅ Complete | `.env.example` |

---

## Bugs & Issues Found

### Critical (P0)

#### 1. `schedule_deliveries` awaits routes SEQUENTIALLY — not concurrent fan-out
**File:** `app/delivery/worker.py` L10-22

```python
# CURRENT (sequential — blocks on each route):
async def schedule_deliveries(event, routes: list[dict]):
    for route in routes:
        await _deliver_with_retry(...)  # blocks until route A finishes before starting route B

# FIX — concurrent fan-out:
import asyncio
async def schedule_deliveries(event, routes: list[dict]):
    tasks = []
    for route in routes:
        body = apply_pipeline(route.get("transform_pipeline") or [], event.request_body)
        tasks.append(_deliver_with_retry(event_id=event.id, route=route, body=body, attempt=0))
    await asyncio.gather(*tasks, return_exceptions=True)
```

> A slow Route A completely blocks Route B. This defeats the fan-out purpose.

#### 2. Circuit breaker exists but is NEVER called in the worker
**File:** `app/delivery/worker.py`

`CircuitBreaker` is fully implemented in `app/delivery/circuit_breaker.py` but is never imported or used in `worker.py`. Every delivery bypasses it entirely.

```python
# Should be wired into _deliver_with_retry:
cb = CircuitBreaker(url)
if await cb.is_open():
    # skip / log / increment failure count
    return
# ... after HTTP call ...
if is_success:
    await cb.record_success()
else:
    await cb.record_failure()
```

#### 3. Rate limiter exists but is NEVER called in the worker
Same gap — `SlidingWindowRateLimiter` exists but `worker.py` never calls `allow_request()` before making HTTP requests.

#### 4. Gateway returns HTTP 200, not 202 Accepted (PRD F-07)
**File:** `app/gateway/handler.py` L86, L18

```python
# CURRENT — FastAPI defaults to 200:
@router.post("/hooks/{endpoint_id}")
async def receive_webhook(...):
    ...
    return {"status": "accepted", ...}

# FIX:
@router.post("/hooks/{endpoint_id}", status_code=202)
```

#### 5. Fire-and-forget `asyncio.ensure_future()` silently swallows exceptions
**File:** `app/gateway/handler.py` L84

```python
# CURRENT — exceptions from schedule_deliveries are silently lost:
asyncio.ensure_future(schedule_deliveries(...))

# FIX — add a done callback to log failures:
task = asyncio.ensure_future(schedule_deliveries(...))
task.add_done_callback(
    lambda t: t.exception() and logger.error("delivery error", exc_info=t.exception())
)
```

---

### Medium (P1)

#### 6. `PUT /api/endpoints/:id` is missing (PRD F-10)
No update endpoint exists. You can create and delete, but not update name, secret, or `is_active`. The `is_active` field is in the model but unreachable via API.

#### 7. `POST /api/endpoints/:id/rotate` secret rotation is missing (PRD F-12)
HMAC secret is set at creation and cannot be rotated.

#### 8. Endpoint toggle `is_active` missing (PRD F-11)
No API exists to enable/disable an endpoint.

#### 9. Admin API returns 500 on invalid UUID (should be 400)
**File:** `app/api/endpoints.py` L37, L46, L56, L70

`uuid.UUID(endpoint_id)` raises `ValueError` on bad input; FastAPI returns a 500. The gateway handler handles this correctly (L30-32), but the admin API doesn't.

```python
# FIX — wrap all UUID parsing:
try:
    ep_id = uuid.UUID(endpoint_id)
except ValueError:
    raise HTTPException(400, "invalid endpoint id format")
```

#### 10. `rate_limit_rpm` missing from `Endpoint` and `Route` DB models
PRD F-13 and DB schema §6.4 require per-endpoint and per-route rate limits. The `Endpoint` model has no `rate_limit_rps` column; `Route` model has no `rate_limit_rpm` column. Only a global default in `config.py` exists.

#### 11. `PUT /api/routes/:id` is missing (PRD F-15)
Route update (change URL, headers, transform, timeout, retries) is not implemented.

---

### Minor / Code Quality (P2-P3)

#### 12. Dashboard is completely empty
`dashboard/src/` has zero files. PRD Phase 1 requires a basic React dashboard with endpoint list and event list view.

#### 13. No tests at all
`tests/unit/` and `tests/integration/` are empty. Zero test coverage.

#### 14. Module docstrings are at the bottom of files (non-standard)
All `"""..."""` docstrings appear after the last line of code. Python's `__doc__` attribute, IDEs, and tools like `pydoc` expect the docstring to be the **first statement** of the module.

#### 15. `requirements.txt` is malformatted (leading spaces + trailing commas)
```
# Current — invalid for pip:
    fastapi>=0.115.0,

# Correct:
fastapi>=0.115.0
```
`pip install -r requirements.txt` would fail. Dependencies appear to work via `pyproject.toml` only.

#### 16. Empty placeholder files
- `app/core/kafka.py` — 0 bytes (fine for Phase 1)
- `workers/transform_worker.py` — 0 bytes (fine for Phase 2)

#### 17. DB session + detached ORM object is a hidden dependency
`expire_on_commit=False` in `async_session_factory` is what makes `event.request_body` accessible after the session closes. This is correct but should be documented since removing it would silently break delivery.

---

## Phase 1 Completion Summary

| Category | Complete | Missing/Broken |
|---|---|---|
| **Core ingestion** | HMAC verify, idempotency, store event | Returns 200 not 202, no CORS (F-05), no IP allowlisting (F-03) |
| **Endpoint CRUD** | Create, List, Get, Delete | PUT (update), secret rotation, toggle active |
| **Route CRUD** | Create, List, Delete | PUT (update route) |
| **Transform engine** | passthrough, template, jmespath | — |
| **Delivery worker** | HTTP call, retry w/ backoff, audit log | Not concurrent, CB not wired, rate limiter not wired |
| **Event API** | List (paginated), Get, Get attempts | Replay (Phase 2 — noted) |
| **Infrastructure** | Docker Compose, .env.example, health check | — |
| **Dashboard** | — | Not started |
| **Tests** | — | Not started |

**Overall Phase 1: ~65-70% complete.** The core pipeline works end-to-end. The critical architectural gap is that delivery is sequential (not fan-out) and two already-written subsystems (circuit breaker, rate limiter) are disconnected from the actual delivery path.

---

## Priority Fix List

| Priority | Fix |
|---|---|
| P0 | `schedule_deliveries` → use `asyncio.gather()` for true concurrent fan-out |
| P0 | Wire `CircuitBreaker` into `_deliver_with_retry` |
| P0 | Add `status_code=202` to the gateway POST route |
| P1 | Wire `SlidingWindowRateLimiter` into `_deliver_with_retry` |
| P1 | Add `PUT /api/endpoints/:id` (update name / is_active) |
| P1 | Add `POST /api/endpoints/:id/rotate` (regenerate HMAC secret) |
| P1 | Add `PUT /api/routes/:id` (update route config) |
| P1 | Wrap `uuid.UUID()` calls in admin API with try/except → 400 |
| P1 | Add done-callback error logging to `asyncio.ensure_future` |
| P2 | Start React dashboard (endpoint list + event list) |
| P2 | Add pytest suite (gateway handler, transform engine, at minimum) |
| P3 | Fix `requirements.txt` formatting |
| P3 | Move module docstrings to top of each file |
