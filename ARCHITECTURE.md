# Architecture & Retrospective

## What This System Does

A self-hosted webhook relay that receives HTTP webhooks from external services (Stripe, GitHub, Slack, etc.), validates them, transforms payloads, fans out to multiple destinations, retries on failure with exponential backoff, and provides full observability into every delivery attempt.

**One-liner:** "Programmable webhook ingress with at-least-once delivery, transform pipelines, and dead-letter queue — all self-hosted."

### The Webhook's Journey

```
GitHub ──POST──▶ Gateway ──▶ PostgreSQL ──▶ Outbox Relay ──▶ Kafka ──▶ Transform Worker ──▶ Kafka ──▶ Delivery Worker ──▶ Your API
                      │                      (outbox table)                      │                                            │
                      └── 202 Accepted ──────┘                                    └── Filters + Transforms ──┘              └── 200/Retry/DLQ
```

1. External service POSTs to `/hooks/{endpoint_id}`
2. Gateway validates signature, rate limit, IP allowlist, JSON Schema, idempotency
3. Creates `Event` + `OutboxRecord` in a single DB transaction → returns `202 Accepted`
4. Outbox Relay polls `outbox_records WHERE status='pending'`, publishes to Kafka `raw-events`
5. Transform Worker consumes, applies route filters + JMESPath/template transforms, publishes to `transformed-events`
6. Delivery Worker consumes, delivers HTTP to destination, retries on failure, circuit breaks on cascading failures, DLQs when exhausted

---

## Every Component and Why

### PostgreSQL
**Why:** The source of truth. Events always exist in the database — Kafka is a notification bus, not the record of truth. If Kafka dies, events are still in Postgres and can be replayed.

**Schema highlights:**
- `events` — status lifecycle `pending → queued → completed/failed`
- `outbox_records` — transactional outbox, `FOR UPDATE SKIP LOCKED` for safe concurrent relay workers
- `delivery_attempts` — immutable audit log (every HTTP request recorded with status, duration, error)
- `endpoints`, `routes`, `users`, `workspaces` — multi-tenant CRUD

### Redis
**Why:** Shared-state coordination that must be fast and ephemeral. Postgres would work but adds latency and connection overhead for high-frequency operations.

**Used for:**
- Sliding-window rate limiter (sorted sets with TTL)
- Circuit breaker state (3-state machine per destination URL)
- Idempotency keys (`SET NX EX` with 24h TTL)
- JWT token blocklist
- Auth rate limiting (per-IP, per-email)

### Kafka
**Why:** Decouples ingestion from processing. The gateway never talks to workers directly. Kafka provides at-least-once delivery semantics, consumer groups for horizontal scaling, and replayability.

**Tradeoff:** Stateful infrastructure. If Kafka is down at startup, `init_kafka()` returns `None` and workers run in degraded mode. The gateway still accepts webhooks (writes to PostgreSQL + outbox), and events drain when Kafka recovers.

**Topics:** `raw-events` → `transformed-events` → `dead-letter`

### FastAPI Gateway (app)
**Why:** Async Python is the right fit for I/O-bound HTTP ingestion. FastAPI's dependency injection cleanly separates signature verification, rate limiting, and RBAC.

**What it does:** Receive, validate, persist, respond. Never blocks on downstream processing.

### Outbox Relay Worker
**Why:** Eliminates the dual-write vulnerability. The original design had the gateway publishing to Kafka directly via `asyncio.create_task` — a crash between `db.commit()` and `kafka.publish()` silently lost events.

**How:** Polls `outbox_records` every 1s with `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 10`, publishes to Kafka, marks `completed`. The `SKIP LOCKED` clause makes it safe to run multiple relay workers concurrently.

### Transform Worker
**Why:** Separate processing from ingestion. Transform pipelines can be CPU-heavy (JMESPath compilation, template rendering) and should not block the hot path.

**Capabilities:** Route filtering (JMESPath boolean expressions), payload transformation (passthrough, JMESPath extraction, template substitution with `{{path/to/key}}` syntax and arithmetic).

### Delivery Worker
**Why:** The most failure-prone part of the system — making HTTP calls to external services. Needs its own process with dedicated resilience logic.

**Resilience patterns:**
- Exponential backoff with jitter: `delay = (base × 2^attempt) + random(0, delay×0.5)`
- Circuit breaker: 3-state (CLOSED/OPEN/HALF-OPEN) per destination URL, stored in Redis
- Rate limiter: sliding window per destination, independent of gateway rate limits
- Dead letter queue: after `max_retries` failures, event goes to `dead-letter` topic

### Retry Scheduler
**Why:** Events with `status=failed` and `retry_at < now()` need automatic re-queuing. This worker polls PostgreSQL for retryable events and re-publishes them to Kafka.

### Dashboard (React + Vite)
**Why:** Visual management of endpoints, routes, events, and delivery attempts. Built with shadcn/ui + Tailwind. Communicates with the REST API only.

### SSRF Protection (`app/core/url_security.py`)
**Why:** Users configure destination URLs. Without protection, an attacker could route traffic to `http://localhost:5432` and exfiltrate the database.

**Layers:**
1. Scheme whitelist (HTTPS required in production)
2. Credential rejection (no `user:pass@` in URLs)
3. Hostname blocklist (localhost, private IPs)
4. DNS resolution at request time (catches DNS rebinding — hostname resolves to public at config time but private at delivery time)

---

## Where It Can Fail

### Gateway Crashes Mid-Request
**Before outbox pattern:** Crash after `db.commit()` but before `kafka.publish()` → event lost forever.

**After outbox pattern:** Crash before `db.commit()` → transaction rolls back. Crash after `db.commit()` → event safe in `outbox_records` as `pending` → outbox relay picks up on restart. **No data loss.**

### Kafka Down
- Gateway still accepts webhooks and writes to PostgreSQL + outbox
- Outbox relay fails to initialize → records stay `pending` until Kafka recovers
- Transform/delivery workers cannot consume → no processing
- **Recovery:** Restart kafka → restart outbox-relay → pending records drain

### PostgreSQL Down
- Gateway cannot read endpoints or write events → all requests fail with 500
- Outbox relay cannot poll → no publishing
- **No mitigation currently implemented.** The gateway has no fallback cache for endpoint config.

### Redis Down
- Rate limiter, circuit breaker, idempotency all fail
- Gateway: requests without idempotency key may still succeed (no Redis check → request proceeds)
- Gateway: requests with idempotency key → Redis call raises, currently unhandled → 500
- Delivery worker: circuit breaker fails open (all requests pass through)
- **Recovery:** Restart Redis → all Redis clients reconnect automatically

### Delivery to Destination Fails
- HTTP 4xx → not retried (client error, retrying won't help)
- HTTP 5xx / timeout / connection error → retried with exponential backoff
- Circuit breaker opens after `circuit_breaker_threshold` consecutive failures → all requests blocked for `cooldown_s` seconds
- After `max_retries` → event goes to dead letter queue

### Outbox Relay Crashes Mid-Publish
- Record is locked with `FOR UPDATE` but transaction rolls back → lock released, record stays `pending`
- Next poll cycle picks it up → **at-least-once delivery** (same event may be published to Kafka twice)
- Idempotency key on the delivery side deduplicates

### Transform Pipeline Fails
- JMESPath expression is invalid → event goes to DLQ with error details
- Template references missing key → empty string substituted, event proceeds
- Pipeline itself crashes (unhandled exception) → event goes to DLQ

### Worker Crash During Shutdown
- **Current state:** `SIGTERM` kills immediately, in-flight deliveries are lost
- **Planned:** Graceful shutdown — on `SIGTERM`, stop consuming, finish in-flight work, commit offsets, then exit

### Network Partition (App ↔ Kafka)
- Producer `send_and_wait` times out → outbox relay increments `attempts`
- After 10 attempts → status = `failed` → needs manual replay or retry scheduler

### Schema Validation Rejects Valid Payload
- Schema too strict → 422 returned, event never created
- **Risk:** Breaking schema change on an existing endpoint causes all webhooks to fail until schema is updated

---

## What We'd Do Differently

### 1. Add Graceful Shutdown from Day 1
The first time we killed a delivery worker mid-request, we lost an in-flight delivery. Signal handling (`SIGTERM` → drain → exit) should be part of every worker template, not an afterthought. It's ~50 lines per worker.

### 2. Use `uuid.uuid4()` Explicitly for PKs
SQLAlchemy's `default=uuid.uuid4` on a column appears to set the attribute at construction time — it doesn't. It fires at flush time. When we needed `event.id` before flush (to set the FK in `OutboxRecord`), it was `None`. This cost a `NOT NULL` constraint violation on first run and 30 minutes of debugging.

**Lesson:** If you need an object's PK before it's flushed, generate the `uuid.uuid4()` explicitly and pass it to the constructor.

### 3. Make Redis Optional / Degrade Gracefully
Currently, if Redis is down, the gateway crashes on any request with an idempotency key (unhandled `RuntimeError` from `get_redis()`). The rate limiter also crashes. Redis should be treated like Kafka — degrade gracefully, log clearly, continue with reduced functionality.

### 4. Add Health Check Endpoints on Workers
Workers (delivery, transform, outbox-relay) have no `/health` endpoint. Docker's `healthcheck` on the app container works, but you can't tell if the delivery worker is alive and consuming. Adding a simple `liveness` (process alive) and `readiness` (connected to Kafka) probe on each worker would make orchestration much safer.

### 5. Use `pyproject.toml` Only (Drop `requirements.txt`)
The project has both `pyproject.toml` (used by Docker build) and `requirements.txt` (outdated, confusing). The Dockerfile uses `pyproject.toml` but we keep editing `requirements.txt` out of habit. Pick one. `pyproject.toml` is the modern standard.

### 6. Don't Mock Everything in Tests
The test suite mocks Redis, Kafka, and PostgreSQL at the module level. Mocked tests pass but give false confidence. The `chaos/` suite uncovered real issues (outbox crash recovery, idempotency races) that unit tests missed. Integration tests with testcontainers or a dedicated test stack catch more bugs than 100 mocked tests.

### 7. Standardize Error Response Format
Some endpoints return `{"detail": "message"}`, others return `{"status": "error", "message": "..."}`. The gateway returns `{"status": "accepted", "event_id": "..."}` on success and `{"detail": "..."}` on errors. This inconsistency makes API client code more fragile than it needs to be.

### 8. Add Structured Logging Earlier
The current logging is `print`-style with `logging.info(f"...")`. No JSON, no correlation IDs, no structured fields. When tracing an event across 4 workers, you grep for `event_id` in plain-text logs. Adding `structlog` or `python-json-logger` with `trace_id` in every log line would have saved hours of debugging.

### 9. Circuit Breaker Should Default to CLOSED on Redis Failure
The circuit breaker stores state in Redis. If Redis is down when the delivery worker starts, `get` returns `None` and the code treats it as CLOSED — which is the correct safe default. But if Redis goes down *while* the circuit is OPEN, the worker loses the OPEN state and starts delivering again (hammering a broken destination).

**Fix:** Persist circuit breaker state locally (in-memory fallback) when Redis is unreachable.

### 10. Don't Use `asyncio.create_task` for Critical Paths
The original gateway used `asyncio.create_task(_publish())` to fire-and-forget Kafka publishing. This is the root cause of the dual-write vulnerability. **Any fire-and-forget async task that touches external infrastructure is a gamble.** If you need post-response processing, use a proper async queue (Kafka, RabbitMQ, or the transactional outbox pattern).
