# Deep Dive: Webhook Relay & Transformation Service

**Target audience:** You've learned FastAPI, know Kafka basics, want to understand how every piece of this project works before building it.

---

## Table of Contents

1. [Core Concepts](#1-core-concepts)
2. [End-to-End Flow (One Webhook's Journey)](#2-end-to-end-flow-one-webhooks-journey)
3. [Component Deep Dives](#3-component-deep-dives)
   - 3.1 Ingestion Gateway
   - 3.2 Transform Engine
   - 3.3 Delivery Worker
   - 3.4 Retry System
   - 3.5 Circuit Breaker
   - 3.6 Rate Limiter
   - 3.7 Dead Letter Queue
   - 3.8 Audit & Observability
4. [Kafka Design](#4-kafka-design)
5. [Database Schema Explained](#5-database-schema-explained)
6. [Key Design Decisions & Tradeoffs](#6-key-design-decisions--tradeoffs)
7. [Difficult Parts (Why They're Hard)](#7-difficult-parts-why-theyre-hard)
8. [Scaling Dimensions](#8-scaling-dimensions)

---

## 1. Core Concepts

### 1.1 What is a webhook? (recap)

A webhook is an HTTP POST sent from one server to another when an event happens. Unlike polling (asking "did something happen?" every 5 seconds), webhooks are push-based — the sender pushes the event to you.

### 1.2 What does "relay" mean here?

```
Stripe ──POST──▶ Your Server ──POST──▶ Slack
                  (receives)      (sends)

Today: Your server does BOTH receiving AND sending in the same code.
Problem: If Slack is slow, your Stripe response is slow. If Slack is down,
         your payment flow breaks.
```

A relay inserts itself in the middle:

```
Stripe ──POST──▶ Relay ──POST──▶ Slack
                  │           ──POST──▶ Analytics
                  │           ──POST──▶ Email
                  │
                  └─▶ Returns 202 instantly

The relay accepts the webhook immediately (202 = "got it, processing"),
queues it, and delivers independently to each destination.
```

### 1.3 Key terms

| Term | Meaning | Example |
|---|---|---|
| **Endpoint** | A named URL that receives webhooks | `Stripe Production` |
| **Route** | A destination + transform rule | `Send Slack message` |
| **Transform** | A rule that reshapes the payload | Strip fields, rename, compute |
| **Event** | One incoming webhook payload | A Stripe `checkout.session.completed` |
| **Delivery attempt** | One HTTP call to one destination | POST to Slack API |
| **DLQ** | Dead Letter Queue — events that failed all retries | |
| **Idempotency key** | A unique string that prevents duplicate processing | |

---

## 2. End-to-End Flow (One Webhook's Journey)

Let's trace a single Stripe webhook through the entire system.

### Setup (done once)

```json
// You configure this through the API/dashboard:
{
  "endpoint": {
    "id": "ep_stripe_1",
    "name": "Stripe Production",
    "secret": "whsec_abc123",
    "routes": [
      {
        "id": "route_slack_1",
        "name": "Notify Slack",
        "url": "https://hooks.slack.com/services/T...",
        "method": "POST",
        "headers": { "Content-Type": "application/json" },
        "transform_rule": {
          "text": "New payment: {{data.customer_email}} paid ${{data.amount_total / 100}}"
        }
      },
      {
        "id": "route_analytics_1",
        "name": "Log to Analytics",
        "url": "https://analytics.myapp.com/events",
        "method": "POST",
        "headers": { "Authorization": "Bearer tok_xyz" },
        "transform_rule": {
          "email": "{{data.customer_email}}",
          "amount": "{{data.amount_total}}",
          "event": "{{type}}"
        }
      }
    ]
  }
}
```

### Step 1: Stripe sends the webhook

```
POST /hooks/ep_stripe_1
Headers:
  X-Hub-Signature-256: sha256=abc123...
  Idempotency-Key: stripe_idem_123
  Content-Type: application/json

Body:
{
  "type": "checkout.session.completed",
  "data": {
    "customer_email": "alice@example.com",
    "amount_total": 2999,
    "currency": "usd"
  }
}
```

### Step 2: Ingestion Gateway (FastAPI handler)

```python
@app.post("/hooks/{endpoint_id}")
async def receive_webhook(endpoint_id, payload, signature, idempotency_key):
    # 2a. Look up endpoint config from PostgreSQL
    endpoint = db.query(Endpoint).get(endpoint_id)
    if not endpoint or not endpoint.is_active:
        return 404

    # 2b. Verify HMAC signature
    # The endpoint has a stored secret. Compute HMAC-SHA256 of the body
    # using that secret and compare with the signature header.
    computed = hmac.new(endpoint.secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"sha256={computed}", signature):
        return 401

    # 2c. Check idempotency (Redis)
    # If we've seen this idempotency_key before, return 409
    # Redis: SET idem:stripe_idem_123 "event_id" EX 86400 NX
    # NX = only set if key doesn't exist
    already_seen = redis.set(f"idem:{idempotency_key}", "1", nx=True, ex=86400)
    if not already_seen:
        return {"status": "duplicate", "event_id": existing_id}

    # 2d. Store the raw event in PostgreSQL
    event = Event(
        endpoint_id=endpoint_id,
        idempotency_key=idempotency_key,
        request_body=payload,
        received_at=utcnow()
    )
    db.add(event)
    db.commit()

    # 2e. Publish to Kafka topic "raw-events"
    # This is a small message containing just what the delivery workers need
    await kafka_producer.send("raw-events", {
        "event_id": event.id,
        "endpoint_id": endpoint_id,
        "received_at": event.received_at.isoformat()
    })

    # 2f. Return 202 immediately
    return {"status": "accepted", "event_id": event.id}, 202
```

**Why return 202 and not 200?** 202 means "I've accepted your request but haven't finished processing." 200 means "here's the result." Since we haven't delivered yet, 202 is the honest and correct status code.

**Total time for Step 2:** ~5-10ms (DB lookup + Redis check + Kafka send). Stripe gets a response almost instantly.

### Step 3: Transform Consumer (Kafka consumer)

A separate Python process (or thread) runs the transform consumer:

```python
async def transform_consumer():
    async for message in consumer:  # reading from "raw-events"
        event_id = message["event_id"]
        endpoint_id = message["endpoint_id"]

        # 3a. Load the full event from PostgreSQL
        event = db.query(Event).get(event_id)
        endpoint = db.query(Endpoint).get(endpoint_id)

        # 3b. For each route, apply the transform
        for route in endpoint.routes:
            transform_rule = route.transform_rule

            if transform_rule is None:
                # Pass-through: send the original payload as-is
                transformed_body = event.request_body
            elif isinstance(transform_rule, dict) and "text" in transform_rule:
                # Template string mode: {{data.field}} → replace with value
                transformed_body = apply_template(transform_rule, event.request_body)
            else:
                # JSONata mode: structured transform
                transformed_body = jsonata.apply(transform_rule, event.request_body)

            # 3c. Publish each transformed payload to "transformed-events"
            # Each becomes a separate delivery task
            await kafka_producer.send("transformed-events", {
                "event_id": event_id,
                "route_id": route.id,
                "url": route.url,
                "method": route.method,
                "headers": {**route.headers},  # merge static + dynamic headers
                "body": transformed_body,
                "timeout_ms": route.timeout_ms,
                "max_retries": route.max_retries,
                "retry_backoff_ms": route.retry_backoff_ms,
                "attempt": 0
            })

        # 3d. Mark the raw event as transformed
        db.commit()
```

**Why separate raw-events and transformed-events topics?**
- It allows you to scale transform and delivery independently
- If transforming is slow (complex JSONata rules), you can run more transform consumers
- If delivery is slow (downstream API is sluggish), you can run more delivery consumers
- You can replay a raw event without re-transforming or re-delivering everything

### Step 4: Delivery Consumer (Kafka consumer)

```python
async def delivery_consumer():
    async for message in consumer:  # reading from "transformed-events"
        # 4a. Check circuit breaker for this destination URL
        # Redis: GET circuit_breaker:https://hooks.slack.com/services/...
        is_open = redis.get(f"circuit_breaker:{message['url']}")
        if is_open:
            # Circuit is open — don't attempt delivery
            # Publish back to "transformed-events" with a delay
            await schedule_retry(message, delay_seconds=30)
            continue

        # 4b. Check rate limit for this destination URL
        # Redis: sliding window counter
        allowed = rate_limiter.check(message["url"], max_rpm=60)
        if not allowed:
            # Publish back with a short delay
            await schedule_retry(message, delay_seconds=1)
            continue

        # 4c. Make the HTTP call
        try:
            start = time.now()
            async with httpx.AsyncClient(timeout=message["timeout_ms"] / 1000) as client:
                resp = await client.request(
                    method=message["method"],
                    url=message["url"],
                    json=message["body"],
                    headers=message["headers"]
                )
            duration = time.now() - start

            # 4d. Log delivery attempt (always)
            db.add(DeliveryAttempt(
                event_id=message["event_id"],
                route_id=message["route_id"],
                attempt_number=message["attempt"],
                request_body=message["body"],
                response_status=resp.status_code,
                response_body=resp.text,
                duration_ms=duration,
                error=None
            ))
            db.commit()

            # 4e. Check if successful
            if 200 <= resp.status_code < 300:
                # Success! Nothing more to do
                pass
            elif 400 <= resp.status_code < 500:
                # Client error (e.g., 400 Bad Request, 401 Unauthorized)
                # This means our request was bad — retrying won't help
                # Log it and move on
                pass
            else:
                # 5xx or network error — retry
                raise DeliveryError(f"HTTP {resp.status_code}")

        except (TimeoutError, ConnectionError, DeliveryError) as e:
            # 4f. Handle failure — decide to retry or DLQ
            attempt = message["attempt"] + 1
            if attempt <= message["max_retries"]:
                # Schedule retry with exponential backoff
                backoff = message["retry_backoff_ms"] * (2 ** (attempt - 1))
                # Add jitter: random(0, backoff) to prevent thundering herd
                backoff = random.randint(0, backoff)
                await schedule_retry(message, delay_ms=backoff)
            else:
                # All retries exhausted — move to Dead Letter Queue
                await kafka_producer.send("dead-letter", message)
```

### Step 5: DLQ Management (via API)

When you click "Replay" in the dashboard:

```python
@app.post("/api/events/{event_id}/replay")
async def replay_event(event_id):
    # Reload the original event from DB
    original_event = db.query(Event).get(event_id)

    # Republish to "raw-events" — goes through the full pipeline again
    await kafka_producer.send("raw-events", {
        "event_id": event_id,
        "endpoint_id": original_event.endpoint_id,
        "received_at": utcnow().isoformat(),
        "is_replay": True
    })

    return {"status": "replaying"}
```

---

## 3. Component Deep Dives

### 3.1 Ingestion Gateway

**File:** `app/gateway.py`

**What it does:**
- Listens on `POST /hooks/{endpoint_id}`
- Validates the webhook (HMAC, IP allowlist, idempotency)
- Stores the raw event in PostgreSQL
- Publishes to Kafka

**Why this structure (thin gateway, heavy workers):**

The ingestion gateway should be as fast as possible. It's the part that receives traffic from external services (Stripe, GitHub, etc.). If it's slow, those services will timeout and retry.

Every millisecond matters here. The gateway should only do:
1. Parse the request
2. Verify the signature (fast — just a hash comparison)
3. Store the event (async write)
4. Publish to Kafka (async write)
5. Return 202

Everything else (transforms, delivery, retries) happens in separate consumers that can scale independently.

**HMAC signature verification in detail:**

```python
import hmac
import hashlib

def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """
    Stripe sends: X-Hub-Signature-256: sha256=computed_hmac

    We compute HMAC-SHA256 of the request body using our stored secret,
    then compare it to the provided signature.

    Important: We use hmac.compare_digest() for constant-time comparison
    to prevent timing attacks. A regular == comparison would leak
    information about how many characters match.
    """
    expected_prefix = "sha256="
    if not signature_header.startswith(expected_prefix):
        return False

    provided_hmac = signature_header[len(expected_prefix):]
    computed_hmac = hmac.new(
        secret.encode(),    # key
        payload,            # message
        hashlib.sha256      # algorithm
    ).hexdigest()

    return hmac.compare_digest(computed_hmac, provided_hmac)
```

**Idempotency key dedup in detail:**

```python
def check_idempotency(redis, key: str, ttl_seconds: int = 86400) -> tuple[bool, str]:
    """
    Idempotency prevents duplicate processing when Stripe retries a webhook.

    Redis:
        SET idempotency:stripe_idem_123 event_id_xyz NX EX 86400

    NX = Only set if key doesn't exist
    EX 86400 = Expire after 24 hours

    If SET returns OK → this is a new request
    If SET returns nil → we've seen this key before → duplicate
    """
    event_id = str(uuid.uuid4())
    result = redis.set(
        f"idempotency:{key}",
        event_id,
        nx=True,  # only set if not exists
        ex=ttl_seconds
    )
    if result:
        return False, event_id  # not a duplicate
    else:
        existing_id = redis.get(f"idempotency:{key}")
        return True, existing_id  # duplicate
```

---

### 3.2 Transform Engine

**File:** `app/transform.py`

**What it does:** Takes a raw webhook payload + a transform rule → outputs a new payload.

**Three transform modes:**

#### Mode 1: Pass-through (no transform)

```python
# Config: transform_rule = null
# Output = exact copy of input
def apply_passthrough(payload: dict) -> dict:
    return payload
```

#### Mode 2: Template string

```python
# Config:
# {
#   "text": "Payment from {{data.customer_email}} for ${{data.amount_total / 100}}"
# }
#
# Uses a simple mustache-like syntax. We parse {{...}} expressions,
# evaluate them against the payload, and replace them.

import re

TEMPLATE_PATTERN = re.compile(r"\{\{(.+?)\}\}")

def apply_template(template_obj: dict, payload: dict) -> dict:
    """
    Takes a template object like:
      {"text": "Hello {{data.name}}"}

    And returns:
      {"text": "Hello Alice"}
    """
    result = {}
    for key, value in template_obj.items():
        if isinstance(value, str):
            # Replace all {{expr}} with evaluated expressions
            def replace_expr(match):
                expr = match.group(1).strip()
                return evaluate_expression(expr, payload)
            result[key] = TEMPLATE_PATTERN.sub(replace_expr, value)
        else:
            result[key] = value
    return result

def evaluate_expression(expr: str, payload: dict) -> str:
    """
    Evaluate simple JMESPath-like expressions against the payload.

    "data.customer_email" → payload["data"]["customer_email"]
    "data.amount_total / 100" → payload["data"]["amount_total"] / 100
    """
    # First try: simple path traversal
    parts = expr.split(".")
    current = payload
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            # Could be a computation like "amount_total / 100"
            try:
                # Safely eval arithmetic expressions
                return str(safe_eval(expr, payload))
            except:
                return f"{{{{{expr}}}}}"
    return str(current)
```

**Important safety note:** NEVER use Python's `eval()` on user-provided expressions. It can execute arbitrary code. Instead, use a restricted expression evaluator or a library like JMESPath that only supports data lookups.

#### Mode 3: JSONata (full structured transforms)

JSONata is a query/transform language designed for JSON. Example:

```json
// Input
{
  "type": "checkout.session.completed",
  "data": {
    "customer_email": "alice@example.com",
    "amount_total": 2999
  }
}

// JSONata expression
{
  "recipient": data.customer_email,
  "subject": "Payment confirmed",
  "body": "Thank you for your payment of $" & (data.amount_total / 100)
}

// Output
{
  "recipient": "alice@example.com",
  "subject": "Payment confirmed",
  "body": "Thank you for your payment of $29.99"
}
```

JSONata is safer than raw JavaScript eval because:
- No system access (no file I/O, no network)
- No loops or recursion that could cause infinite execution
- Purpose-built for JSON data
- Predictable performance

---

### 3.3 Delivery Worker

**File:** `app/delivery.py`

**What it does:** Reads from `transformed-events` Kafka topic and makes HTTP calls.

**Key design: Concurrency control**

```python
import asyncio
import httpx

class DeliveryWorker:
    def __init__(self, max_concurrent=50):
        # Semaphore limits how many concurrent HTTP calls we make
        # This prevents the worker from overwhelming itself or the network
        self.semaphore = asyncio.Semaphore(max_concurrent)
        # Per-destination concurrency limit
        self.destination_semaphores = {}

    async def deliver(self, message):
        # First, acquire the global semaphore
        async with self.semaphore:
            # Then, acquire per-destination semaphore
            # This ensures we don't hammer a single endpoint
            dest_key = message["url"]
            if dest_key not in self.destination_semaphores:
                self.destination_semaphores[dest_key] = asyncio.Semaphore(5)

            async with self.destination_semaphores[dest_key]:
                await self._make_http_call(message)

    async def _make_http_call(self, message):
        async with httpx.AsyncClient() as client:
            # httpx supports connection pooling — reuse TCP connections
            # to the same host for faster subsequent requests
            resp = await client.request(
                method=message["method"],
                url=message["url"],
                json=message["body"],
                headers=message["headers"],
                timeout=message["timeout_ms"] / 1000
            )
        return resp
```

**Why asyncio.Semaphore?** Without it, if 1000 events arrive at once, the worker would open 1000 concurrent TCP connections. That can:
- Exhaust system file descriptors (ulimit -n)
- Overwhelm the network stack
- Trigger DDoS protection on the destination API
- Cause the worker itself to run out of memory

The semaphore acts as a pressure valve.

---

### 3.4 Retry System

**The retry flow:**

```
Attempt 0: Immediate delivery
    ↓ (fail)
Attempt 1: Wait 1s (backoff_ms × 2^0 + jitter)
    ↓ (fail)
Attempt 2: Wait 2s (backoff_ms × 2^1 + jitter)
    ↓ (fail)
Attempt 3: Wait 4s
    ↓ (fail)
Attempt 4: Wait 8s
    ↓ (fail)
Attempt 5: Wait 16s
    ↓ (fail)
Move to Dead Letter Queue
```

**Exponential backoff with jitter:**

```python
import random

def calculate_retry_delay(attempt: int, base_ms: int = 1000) -> int:
    """
    Exponential backoff: base_ms × 2^(attempt - 1)
    With jitter: random(0, backoff)

    Jitter prevents the "thundering herd" problem:
    If 100 events all fail at the same time, without jitter
    they all retry at exactly the same time, overloading the
    destination again. Jitter spreads them out.
    """
    backoff = base_ms * (2 ** (attempt - 1))
    jitter = random.randint(0, backoff)
    return jitter
```

**How retries work with Kafka:**

When a delivery fails and needs retry, we have two options:

**Option A: Publish back to the same topic with a delay**
```python
# Not natively supported by Kafka (no delayed delivery)
# Workaround: use a separate "retry" topic or Redis-based scheduler
```

**Option B: Use a retry scheduler**
```python
# 1. When delivery fails, instead of publishing to Kafka,
#    store a "retry task" in PostgreSQL or Redis
#
# 2. A separate "retry scheduler" process wakes up every second,
#    checks for due retries, and publishes them back to
#    "transformed-events" for redelivery

@app.task  # runs every second
async def retry_scheduler():
    due_retries = db.query(RetryTask).filter(
        RetryTask.next_attempt_at <= utcnow(),
        RetryTask.attempts < RetryTask.max_retries
    ).all()

    for task in due_retries:
        # Publish back to the delivery topic
        await kafka_producer.send("transformed-events", task.message)
        # Delete the retry task (or mark as queued)
        db.delete(task)

    db.commit()
```

**Option C: Kafka with retry topics** (most sophisticated)

```
transformed-events (main)
    ↓
delivery_attempt(event)
    ↓ (fail)
retry-1s-topic (consumed by delivery worker, but with 1s pause)
    ↓ (fail)
retry-2s-topic
    ↓ (fail)
retry-4s-topic
    ↓ (fail)
... eventually → dead-letter topic
```

Each retry topic has a different `ConsumerConfig` that introduces a delay before processing. This is how production systems like Svix handle retries with Kafka. It's more complex but fully Kafka-native.

**For your project, start with Option B** (PostgreSQL/Redis scheduler). It's simpler to understand and debug. Upgrade to Option C when you understand Kafka well enough to need it.

---

### 3.5 Circuit Breaker

**What it protects against:** A downstream API (e.g., Slack) goes down. Without a circuit breaker, every delivery worker keeps hammering Slack's broken API, wasting connections, time, and making the problem worse.

**The three states:**

```
        ┌──────────┐
   ┌───▶│  CLOSED  │ (normal operation — requests pass through)
   │    └─────┬────┘
   │          │ N consecutive failures
   │          ▼
   │    ┌──────────┐
   │    │   OPEN   │ (failing fast — requests are rejected immediately)
   │    └─────┬────┘
   │          │ After cooldown period
   │          ▼
   │    ┌──────────┐
   │    │  HALF-   │ (testing the waters — allow 1 request through)
   │    │  OPEN    │
   │    └─────┬────┘
   │          │
   │     ┌────┴────┐
   │     │         │
   │  success   failure
   │     │         │
   └─────┘         └──▶ OPEN again
```

**Implementation with Redis:**

```python
class CircuitBreaker:
    def __init__(self, redis, destination_url: str):
        self.redis = redis
        self.url = destination_url
        self.failure_threshold = 10  # N failures before opening
        self.cooldown_seconds = 30   # how long to stay open
        self.half_open_timeout = 5   # how long to wait in half-open

    async def is_open(self) -> bool:
        """
        Returns True if the circuit is OPEN (don't attempt delivery).
        """
        state = await self.redis.get(f"circuit:{self.url}:state")
        if state is None:
            return False  # CLOSED

        state = state.decode()
        if state == "OPEN":
            # Check if cooldown has expired
            open_since = float(
                await self.redis.get(f"circuit:{self.url}:open_since") or 0
            )
            elapsed = time.time() - open_since
            if elapsed >= self.cooldown_seconds:
                # Transition to HALF-OPEN
                await self.redis.set(f"circuit:{self.url}:state", "HALF_OPEN")
                return False  # Allow this one request through
            return True  # Still OPEN

        if state == "HALF_OPEN":
            # We're allowing a single request through to test
            # Use a flag to prevent multiple concurrent test requests
            tested = await self.redis.setnx(
                f"circuit:{self.url}:half_open_tested", "1"
            )
            if tested:
                return False  # This request is the test
            return True  # Another request is already testing

        return False

    async def record_success(self):
        """Call after successful delivery."""
        await self.redis.delete(
            f"circuit:{self.url}:state",
            f"circuit:{self.url}:failure_count",
            f"circuit:{self.url}:open_since",
            f"circuit:{self.url}:half_open_tested"
        )

    async def record_failure(self):
        """Call after failed delivery."""
        pipe = self.redis.pipeline()
        pipe.incr(f"circuit:{self.url}:failure_count")
        pipe.expire(f"circuit:{self.url}:failure_count", 60)
        count = await pipe.execute()
        count = count[0]

        if count >= self.failure_threshold:
            # Open the circuit
            await self.redis.set(f"circuit:{self.url}:state", "OPEN")
            await self.redis.set(
                f"circuit:{self.url}:open_since", str(time.time())
            )
```

---

### 3.6 Rate Limiter

**What it does:** Limits how many requests per minute we send to a single destination URL.

**Algorithm: Sliding Window (Redis sorted sets)**

```python
import time

class SlidingWindowRateLimiter:
    """
    Tracks requests per destination in a sliding 60-second window.

    Uses Redis Sorted Set where:
    - member = unique request ID
    - score = timestamp in milliseconds

    To check: count members with score > (now - 60s)
    If count < limit → allow, add member
    If count >= limit → reject
    """

    def __init__(self, redis, max_rpm: int = 60):
        self.redis = redis
        self.max_rpm = max_rpm
        self.window_ms = 60_000  # 60 seconds

    async def allow_request(self, destination_url: str) -> bool:
        now = time.time() * 1000  # milliseconds
        window_start = now - self.window_ms
        key = f"ratelimit:{destination_url}"

        # Use Redis pipeline for atomicity
        pipe = self.redis.pipeline()

        # Remove entries outside the window
        pipe.zremrangebyscore(key, 0, window_start)

        # Count entries in the window
        pipe.zcard(key)

        # Add current request
        pipe.zadd(key, {str(uuid.uuid4()): now})

        # Set TTL on the key (cleanup)
        pipe.expire(key, 60)

        results = await pipe.execute()
        count = results[1]  # zcard result

        # Allow if under limit (minus 1 because we already added)
        return (count - 1) < self.max_rpm

    async def check_only(self, destination_url: str) -> bool:
        """Check without recording the request (for pre-checks)."""
        now = time.time() * 1000
        window_start = now - self.window_ms
        key = f"ratelimit:{destination_url}"

        # Remove old entries and count
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.expire(key, 60)
        results = await pipe.execute()

        return results[1] < self.max_rpm
```

**Why Redis for this?**
- Atomic operations (ZADD + ZREMRANGEBYSCORE in one command)
- Built-in TTL for auto-cleanup
- Fast (sub-millisecond)
- Shared across all worker instances (unlike in-memory counters)

---

### 3.7 Dead Letter Queue

**What it is:** A Kafka topic (`dead-letter`) where events go after exhausting all retries.

**DLQ message structure:**

```json
{
  "event_id": "evt_abc123",
  "route_id": "route_slack_1",
  "url": "https://hooks.slack.com/services/...",
  "body": {"text": "New payment: alice@example.com paid $29.99"},
  "attempts": 5,
  "attempt_history": [
    {"attempt": 0, "status": 500, "error": "Internal Server Error", "at": "..."},
    {"attempt": 1, "status": 502, "error": "Bad Gateway", "at": "..."},
    {"attempt": 2, "status": null, "error": "Connection timeout", "at": "..."},
    {"attempt": 3, "status": 503, "error": "Service Unavailable", "at": "..."},
    {"attempt": 4, "status": null, "error": "Connection reset", "at": "..."},
  ],
  "moved_to_dlq_at": "2026-05-21T10:30:00Z"
}
```

**Why have a DLQ?** If you didn't have one, events that fail all retries would just be lost (you "drop" them by not processing further). With a DLQ, you have:
- A place to inspect them
- The ability to replay them
- The ability to alert on them
- A safety net for when things go wrong

**DLQ replay:**
```python
@app.post("/api/dead-letter/{dlq_id}/replay")
async def replay_dlq_event(dlq_id: str):
    # Fetch the DLQ event from PostgreSQL or Kafka
    dlq_event = get_dlq_event(dlq_id)

    # Publish back to "transformed-events" with attempt=0
    await kafka_producer.send("transformed-events", {
        "event_id": dlq_event["event_id"],
        "route_id": dlq_event["route_id"],
        "url": dlq_event["url"],
        "method": dlq_event["method"],
        "headers": dlq_event["headers"],
        "body": dlq_event["body"],
        "timeout_ms": dlq_event["timeout_ms"],
        "max_retries": dlq_event["max_retries"],
        "retry_backoff_ms": dlq_event["retry_backoff_ms"],
        "attempt": 0,
        "is_replay": True
    })

    # Remove from DLQ
    delete_dlq_event(dlq_id)

    return {"status": "replayed"}
```

---

### 3.8 Audit & Observability

**The audit table:**

```sql
CREATE TABLE delivery_attempts (
    id UUID PRIMARY KEY,
    event_id UUID REFERENCES events(id),
    route_id UUID REFERENCES routes(id),
    attempt_number INT,
    request_url TEXT,
    request_method TEXT,
    request_headers JSONB,
    request_body JSONB,
    response_status INT,
    response_headers JSONB,
    response_body TEXT,
    error TEXT,
    duration_ms INT,
    attempted_at TIMESTAMPTZ DEFAULT NOW()
);
```

Every single HTTP call made by the system is logged here. This enables:

1. **Event timeline view:** "What happened to event evt_abc123?"
   ```
   received → transformed → attempt 0 (500, 42ms) → attempt 1 (200, 15ms) ✓
   ```

2. **Aggregated stats:** "What's our delivery success rate?"
   ```sql
   SELECT
     route_id,
     COUNT(*) as total,
     SUM(CASE WHEN response_status BETWEEN 200 AND 299 THEN 1 ELSE 0 END) as success,
     AVG(duration_ms) as avg_duration
   FROM delivery_attempts
   WHERE attempted_at > NOW() - INTERVAL '1 day'
   GROUP BY route_id;
   ```

3. **Failure analysis:** "Why are events to Slack failing?"
   ```sql
   SELECT response_status, error, COUNT(*)
   FROM delivery_attempts
   WHERE route_id = 'route_slack_1'
     AND attempted_at > NOW() - INTERVAL '1 hour'
     AND (response_status < 200 OR response_status >= 300)
   GROUP BY response_status, error;
   ```

4. **Latency breakdown:**
   ```sql
   SELECT
     PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) as p50,
     PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95,
     PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) as p99
   FROM delivery_attempts
   WHERE attempted_at > NOW() - INTERVAL '1 hour';
   ```

---

## 4. Kafka Design

### 4.1 Topic Structure

```
Topic:            raw-events
Partitions:       3
Replication:      3 (in production), 1 (in dev)
Retention:        7 days
Cleanup policy:   delete
Key:              endpoint_id (so events for same endpoint go to same partition)
Value:            { event_id, endpoint_id, received_at }

Topic:            transformed-events
Partitions:       6 (more partitions = more parallel consumers)
Replication:      3
Retention:        7 days
Key:              route_id (so same route stays ordered)
Value:            { event_id, route_id, url, method, headers, body, ... }

Topic:            dead-letter
Partitions:       1
Replication:      3
Retention:        30 days
Value:            { event_id, route_id, url, ..., attempt_history, ... }
```

### 4.2 Consumer Groups

```
Group:            transform-workers
Subscribes to:    raw-events
Max consumers:    3 (= number of partitions)
Each consumer:    owns 1+ partitions

Group:            delivery-workers
Subscribes to:    transformed-events
Max consumers:    6 (= number of partitions)
Each consumer:    owns 1+ partitions
```

**Why consumer groups matter:** When you have multiple consumers in the same group, Kafka assigns each partition to exactly one consumer. If you have 6 partitions and 3 consumers, each consumer gets 2 partitions. If one consumer crashes, Kafka rebalances — the remaining consumers take over the crashed one's partitions.

### 4.3 Producer Configuration

```python
producer = aiokafka.AIOKafkaProducer(
    bootstrap_servers=KAFKA_BROKERS,
    acks="all",       # Wait for all in-sync replicas to acknowledge
    compression_type="gzip",  # Compress messages (webhook payloads can be large)
    retries=5,        # Retry on transient broker errors
    batch_size=16384, # Batch messages for efficiency
    linger_ms=10,     # Wait 10ms for more messages before sending
)
```

**`acks="all"`** means the producer waits for all replicas to confirm they've received the message. This ensures no data loss even if a broker crashes. Tradeoff: slightly higher latency (~1-2ms).

**`linger_ms=10`** means the producer waits up to 10ms to batch messages. This dramatically improves throughput (sending 100 messages in one batch is much faster than 100 individual sends).

### 4.4 Consumer Configuration

```python
consumer = aiokafka.AIOKafkaConsumer(
    "transformed-events",
    bootstrap_servers=KAFKA_BROKERS,
    group_id="delivery-workers",
    enable_auto_commit=False,  # We manually commit offsets
    auto_offset_reset="earliest",  # Start from beginning on new group
    max_poll_records=100,     # Don't fetch too many at once
)
```

**`enable_auto_commit=False`** is critical. If auto-commit is on, Kafka commits the offset as soon as the consumer receives the message — even if the delivery fails. If the consumer crashes, the message is "committed" but never delivered. By manually committing, we ensure we only commit AFTER successful delivery.

```python
async for message in consumer:
    try:
        await deliver(message)
        # Only commit after successful delivery
        await consumer.commit()
    except Exception:
        # Don't commit — message will be redelivered
        pass
```

### 4.5 The "At Least Once" Guarantee

Kafka's architecture gives us **at-least-once delivery** (not exactly-once):

1. Consumer receives message
2. Consumer attempts delivery
3a. Delivery succeeds → commit offset → done
3b. Delivery succeeds → crash before commit → message is redelivered to another consumer
3c. Delivery fails → don't commit → message is redelivered

This means events CAN be delivered more than once. This is why idempotency is important — the destination should handle duplicates gracefully (by using the idempotency key).

---

## 5. Database Schema Explained

### 5.1 ERD (Entity Relationship Diagram)

```
┌─────────────┐       ┌────────────────┐
│  endpoints  │──1:N──│    routes      │
└─────────────┘       └────────────────┘
       │
       │1:N
       ▼
┌─────────────┐       ┌────────────────────┐
│   events    │──1:N──│ delivery_attempts  │
└─────────────┘       └────────────────────┘
```

### 5.2 Why this schema?

**endpoints separate from routes:**
- One webhook URL can have N destinations
- Routes can be added/removed without changing the endpoint URL
- If a destination changes (new Slack URL), you only update the route
- Enables "disable a route" without losing the endpoint

**events separate from delivery_attempts:**
- One webhook → multiple delivery attempts (one per retry)
- One webhook → multiple delivery_attempts across different routes
- Events are immutable (append-only), which is great for audit

**JSONB for request/response bodies:**
- Webhook payloads are unpredictable (different shapes from different providers)
- JSONB allows indexing and querying inside the JSON
- No schema migration needed when a provider changes their payload

### 5.3 Partitioning for scale

When you have millions of events, queries like `SELECT * FROM events WHERE received_at > '2026-01-01'` will be slow. Solution: partition the events table by date.

```sql
-- Create a partitioned table
CREATE TABLE events (
    id UUID,
    endpoint_id UUID,
    request_body JSONB,
    received_at TIMESTAMPTZ
) PARTITION BY RANGE (received_at);

-- Create monthly partitions
CREATE TABLE events_2026_01
    PARTITION OF events
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE events_2026_02
    PARTITION OF events
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
```

PostgreSQL will automatically route queries to the right partition. A query with `WHERE received_at > '2026-01-15'` will only scan the relevant partitions, not the entire table.

---

## 6. Key Design Decisions & Tradeoffs

### 6.1 Why FastAPI and not Node/Express?

| Factor | FastAPI | Express |
|---|---|---|
| Async | Native asyncio | Callback-based (async/await added later) |
| Validation | Pydantic (built-in) | Manual or Joi/Zod |
| Auto-docs | OpenAPI (built-in) | swagger-jsdoc |
| Kafka client | aiokafka (mature) | node-rdkafka (C++ binding) |
| Type safety | Pydantic + mypy | TypeScript (better than Python here) |
| Concurrency | asyncio (cooperative) | Worker threads (single-threaded event loop) |

The honest answer: both would work. FastAPI was chosen because you're learning it.

### 6.2 Why Kafka and not Redis Queue / RabbitMQ?

| Factor | Kafka | Redis Queue | RabbitMQ |
|---|---|---|---|
| Message persistence | Disk (configurable) | Memory + disk (RDB/AOF) | Disk |
| Message replay | ✅ (from any offset) | ❌ | ❌ (ack = deleted) |
| Consumer groups | ✅ (native) | ❌ | ✅ |
| Ordering | ✅ (per partition) | ❌ | ✅ |
| Throughput | 1M+ msg/s | 100K msg/s | 100K msg/s |
| Operational complexity | Higher | Low | Medium |

Kafka was chosen because:
- You said you just learned it — this project reinforces that knowledge
- The ability to replay messages from any point in time is powerful for debugging
- Consumer groups make horizontal scaling natural
- Dead letter topics are a first-class concept

### 6.3 Why two Kafka topics (raw + transformed)?

Why not one topic and do transforms in the delivery worker?

**Option 1: Single topic, inline transform**
```
raw-events → delivery worker (transforms + delivers)
```
- Simpler: one consumer, one topic
- Problem: if either transform or delivery is slow, both suffer
- If the transform crashes, the delivery attempt is lost

**Option 2: Two topics, separate workers**
```
raw-events → transform worker → transformed-events → delivery worker
```
- More complex (one more topic, one more consumer group)
- Transform and delivery can scale independently
- If delivery crashes, the transformed payload is still in the topic
- You can replay delivery without re-transforming

**Verdict:** Start with Option 1 for Phase 1 (when there's no Kafka), then move to Option 2 when you add Kafka in Phase 2. The two-topic architecture is the professional version — learn it when you need it.

### 6.4 Why return 202 and not store the event synchronously?

**Option A:** Store in DB → return 202 (current design)
- Response time: ~5ms
- Risk: if Kafka/DB fails after returning 202, event is lost
- Mitigation: idempotency key means Stripe will retry

**Option B:** Store in DB → Kafka → return 202 — but use an outbox pattern
```python
# 1. Insert event + "outbox" record in a TRANSACTION
# 2. A separate process reads the outbox and publishes to Kafka
# 3. Only delete from outbox after Kafka confirms
```
- Guarantees no event loss (the outbox acts as a safety net)
- More complex (needs a background process to poll the outbox)
- This is the "transactional outbox" pattern — used by Hookflow

For your project, Option A is fine. If this were a bank processing payments, you'd want Option B.

### 6.5 Why per-destination rate limiting and circuit breakers?

```
Without rate limiting:         With rate limiting:
Stripe sends 100 events ──▶   Stripe sends 100 events ──▶
    │ Each worker sends           │ Workers send 60/min to Slack
    │ HTTP call to Slack          │ Remaining 40 are queued
    ▼                             ▼
Slack gets 100 requests/sec  Slack gets steady 1/sec
Slack rate-limits us         Slack is happy
We get 429 errors            Events delivered
```

Circuit breaker extends this: if Slack returns 5xx repeatedly, don't even try — fail fast. This:
- Prevents wasting resources on doomed requests
- Gives Slack time to recover
- Reduces backpressure on the rest of the system

---

## 7. Difficult Parts (Why They're Hard)

### 7.1 Handling downstream API rate limits

**The problem:** Slack allows 1 request per second. You have 50 delivery workers. Without coordination, all 50 workers can hit Slack simultaneously and get 429 rate-limited.

**The solution:** Two-layer rate limiting:
1. **Global counter** (Redis): tracks all requests to Slack across all workers
2. **Local semaphore** (in-process): limits concurrent requests to the same host

But even with this, you have a race condition:
```
Worker A: check rate limit → 59/60 used → OK, proceed
Worker B: check rate limit → 59/60 used → OK, proceed  (same count!)
Worker A: send request → count becomes 60/60
Worker B: send request → count becomes 61/60  (oops!)
```

**Fix:** Use Redis Lua scripts for atomic check-and-increment:

```lua
-- Redis Lua script: atomically check and increment
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local window = tonumber(ARGV[3])

-- Remove old entries
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

-- Count current entries
local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now, uuid_generate())
    redis.call('EXPIRE', key, window / 1000)
    return { allowed = true, remaining = limit - count - 1 }
else
    return { allowed = false, remaining = 0 }
end
```

Lua scripts run atomically in Redis — no race conditions.

### 7.2 Circuit breaker state sync across instances

**The problem:** You have 3 delivery worker instances. Instance A opens the circuit to Slack. Instance B doesn't know about it and keeps trying.

**The solution:** Store circuit breaker state in Redis (shared across all instances). Every worker checks Redis before attempting delivery.

Edge case: What if Redis goes down?
- **Option A:** Fail open (allow all requests) — risk: hammering the downstream
- **Option B:** Fail closed (block all requests) — risk: blocking valid deliveries
- **Recommendation:** Fail open with a warning log. It's better to deliver (and possibly get rate-limited) than to silently drop events.

### 7.3 Kafka exactly-once vs at-least-once

**The problem:** You want "exactly once" delivery — each event is delivered exactly one time. But Kafka gives you at-least-once: events CAN be delivered multiple times.

**Why exactly-once is impossible:**
```
1. Consumer receives message
2. Consumer delivers to destination
3. Destination acknowledges (200 OK)
4. Consumer commits offset
5. Consumer crashes BEFORE commit
```

Step 5 means the message will be redelivered to another consumer, who will deliver it again. The destination gets the same webhook twice.

**The solution is NOT to prevent duplicates — it's to handle them:**
1. **Idempotency key**: include a unique key in every delivery attempt. The destination uses this to deduplicate.
2. **At-least-once guarantee with idempotent receivers**: the standard webhook pattern.

> "There are only two hard problems in distributed systems: 2. Exactly-once delivery, and 1. At-least-once delivery with idempotent receivers." — (loosely) Mathias Verraes

### 7.4 Transform sandboxing

**The problem:** Users write transform expressions like:
```
// Innocent:
{ "name": data.name }

// Malicious:
{ "name": eval("process.env.PASSWORD") }  // can't allow eval!
```

**The solution:** Never use `eval()` or `exec()`. Use:
1. **JMESPath** — a restricted query language for JSON. No functions, no computation, just field access and projections.
2. **JSONata** — supports computations (math, string concat) but NO system access.
3. **Template strings** — the simplest option, just `{{field.path}}` replacement.

**Safety checklist:**
```python
# Safe: JMESPath expressions
result = jmespath.search(expr, payload)  # only data access

# Also safe: JSONata
result = jsonata.apply(expr, payload)  # no system access

# DANGER: never do this
result = eval(expr)  # arbitrary code execution!
```

### 7.5 Backpressure

**The problem:** Stripe sends 10,000 webhooks in one second. Your system has to absorb this burst without crashing.

**The solution:** Backpressure at multiple levels:

```
Level 1: Kafka (primary buffer)
  - Kafka can absorb millions of messages
  - The topic acts as a giant buffer
  - Workers consume at their own pace

Level 2: Ingress rate limiting (optional)
  - Reject excess requests with 429 Too Many Requests
  - Stripe will retry with backoff

Level 3: Async work queues
  - FastAPI uses asyncio, which handles many concurrent connections
  - Each connection yields while waiting for Kafka → handles more connections
```

**The key insight:** With Kafka, the ingestion gateway is NEVER the bottleneck. It receives a message, publishes to Kafka, and returns 202 in ~5ms. It can handle 10,000 requests/second on a single instance because it's doing almost no work.

---

## 8. Scaling Dimensions

### 8.1 Vertical scaling (bigger machine)

| Component | What to increase |
|---|---|
| FastAPI | CPU cores (works for asyncio) |
| PostgreSQL | RAM (more cache = faster queries) |
| Kafka | Disk IOPS + RAM (page cache) |
| Redis | RAM (it's an in-memory DB) |

### 8.2 Horizontal scaling (more instances)

| Component | How |
|---|---|
| FastAPI | Multiple instances behind a load balancer (stateless) |
| Transform workers | Add more consumers in the same group (max = partitions) |
| Delivery workers | Add more consumers in the same group (max = partitions) |
| Kafka | Add more brokers, increase partitions |
| PostgreSQL | Read replicas (for dashboard queries) |

### 8.3 When to scale

| Symptom | Likely cause | Fix |
|---|---|---|
| Ingestion latency > 50ms | Kafka producer slow | Increase `linger_ms`, increase batch size |
| Delivery backlog growing | Too few delivery workers | Increase delivery worker count |
| Transform backlog growing | Too few transform workers | Increase transform worker count |
| Dashboard queries slow | Too many events in DB | Partition events table by month |
| Redis memory high | Too many rate limit counters | Reduce TTL on rate limit keys |

---

## Quick Reference: What to Build (Phase 1, no Kafka)

```
┌──────────────┐
│  FastAPI     │  ← Stripe sends webhook here
│  /hooks/{id} │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  PostgreSQL  │  ← Store the raw event
│  events      │
│  table       │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Transform   │  ← In-process (same Python process)
│  Engine      │     Reads event from DB, applies transforms
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Delivery    │  ← In-process background task
│  Worker      │     Makes HTTP call, handles retries
└──────┬───────┘
       │
       ▼
    Slack API
```

Phase 1 is simpler: no Kafka, no separate consumers. The ingestion handler stores the event, applies transforms, and spawns a background task (`asyncio.create_task`) for delivery. This is enough for small-scale use and teaches you the core flow.

Phase 2 (Kafka) replaces the direct DB → transform → delivery chain with Kafka topics and separate consumer processes.

---

*This deep dive will make more sense as you build. Don't try to absorb it all at once — refer back to specific sections when you hit each problem in practice.*
