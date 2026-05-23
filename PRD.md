# PRD: Webhook Relay & Transformation Service

**Status:** Draft v1
**Author:** [You]
**Date:** 2026-05-21

---

## 1. Executive Summary

A self-hostable webhook relay that receives incoming webhooks, transforms payloads using configurable rules, fans out to multiple destinations, handles retries with exponential backoff, and provides full observability into every delivery attempt.

**One-liner:** "Zapier for webhooks — self-hosted, built for ops, no vendor lock-in."

---

## 2. Problem Statement

### 2.1 The Pain

Every application that integrates with external services eventually needs to:

1. Receive a webhook from Service A (e.g., Stripe payment event)
2. Notify Service B, C, and D (e.g., analytics, Slack, email)
3. Each destination needs a DIFFERENT payload shape
4. Each destination has different reliability characteristics
5. Failures must be retried, logged, and debuggable

### 2.2 Current Solutions (and their gaps)

| Approach | Problems |
|---|---|
| Handle synchronously in request handler | One slow/failing downstream crashes your endpoint |
| Background tasks (Celery/ARQ) | No built-in webhook transform engine, no delivery-specific observability |
| Raw Kafka consumers | You rebuild retry logic, DLQ management, and a monitoring UI every time |
| Zapier / Make / Pipedream | Hosted, expensive at scale, cannot self-host, data leaves your network |
| AWS EventBridge | Vendor lock-in, complex, no built-in transform engine |

### 2.3 Target Users

- **Solo devs** who want a fire-and-forget webhook pipeline without duct-tape
- **Platform teams** managing multi-service event fan-out
- **Agencies** shipping multiple projects that all need webhook infrastructure
- **Open source projects** that need to receive and relay webhooks reliably

---

## 3. Product Overview

### 3.1 What It Does

```
  [Stripe] ──POST──▶  ┌──────────────────┐  ──POST──▶ [My App]
  [GitHub]  ──POST──▶  │  Webhook Relay   │  ──POST──▶ [Slack]
  [Custom]  ──POST──▶  │  Service         │  ──POST──▶ [Analytics]
                       └──────────────────┘  ──POST──▶ [Email]
```

- **Ingress**: Accept webhooks via HTTP POST at a unique endpoint URL
- **Transform**: Mutate the payload per-destination using expression rules (JSONata/JMESPath)
- **Route**: Fan-out to multiple destinations from a single incoming webhook
- **Deliver**: Make HTTP calls with configurable method, headers, auth
- **Retry**: Exponential backoff with configurable max attempts
- **Observe**: Full audit trail of every event, every delivery attempt, every failure
- **Recover**: Dead letter queue with manual replay

### 3.2 Non-Goals

- Not a general-purpose event bus / message broker
- Not a replacement for Kafka/RabbitMQ
- Not a webhook *sender* (does not originate webhooks)
- Not a webhook *generator* (does not poll APIs for changes)

---

## 4. Functional Requirements

### 4.1 Ingestion (`P0`)

| ID | Requirement | Notes |
|---|---|---|
| F-01 | Receive HTTP POST requests at a unique endpoint URL | Format: `/hooks/{endpoint_id}` |
| F-02 | Support configurable HMAC signature verification | SHA256, configurable header name |
| F-03 | Support IP allowlisting for incoming requests | Optional, CIDR format |
| F-04 | Idempotency key deduplication | Optional header, configurable TTL via Redis |
| F-05 | Support CORS for browser-initiated requests | Configurable origins |
| F-06 | Accept any Content-Type (JSON, form-encoded, plain text) | Parse accordingly |
| F-07 | Return 202 Accepted immediately | Never block on downstream processing |
| F-08 | Return 409 Conflict on duplicate idempotency key | If idempotency enabled |

### 4.2 Endpoint Management (`P0`)

| ID | Requirement | Notes |
|---|---|---|
| F-09 | CRUD API for webhook endpoints | Create, read, update, delete |
| F-10 | Each endpoint has: name, secret, routes[], is_active | |
| F-11 | Toggle endpoint active/inactive | Inactive endpoints return 404 |
| F-12 | Rotate HMAC secret | Invalidate old signature immediately |
| F-13 | Per-endpoint rate limiting | Configurable requests/second |

### 4.3 Route Configuration (`P0`)

| ID | Requirement | Notes |
|---|---|---|
| F-14 | Each endpoint has one or more routes | |
| F-15 | Each route has: url, method, headers, transform_rule | |
| F-16 | Transform rule maps incoming payload → new payload | JSONata or JMESPath |
| F-17 | Support injecting static headers | E.g., `Authorization: Bearer xxx` |
| F-18 | Support dynamic headers from transformed payload | |
| F-19 | Per-route timeout | Default 10s, configurable |
| F-20 | Per-route retry config | Max attempts, backoff multiplier |

### 4.4 Transformation Engine (`P1`)

| ID | Requirement | Notes |
|---|---|---|
| F-21 | Sandboxed execution of transform expressions | No `eval()`, no system access |
| F-22 | Support template strings with variable interpolation | e.g., `{{data.email}}` |
| F-23 | Support structured transforms (object → object) | Using JSONata |
| F-24 | Precompile and cache transform expressions | Performance optimization |
| F-25 | Validate transform rules at config time | Fail fast, not at runtime |
| F-26 | Default pass-through transform (send original payload) | $passthrough |

### 4.5 Delivery Engine (`P0`)

| ID | Requirement | Notes |
|---|---|---|
| F-27 | Async HTTP delivery for each route | Using httpx/aiohttp |
| F-28 | Exponential backoff on failure | Default: 1s, 2s, 4s, 8s... max 5 retries |
| F-29 | Distinguish retriable vs non-retriable errors | 5xx/timeout → retry, 4xx → don't retry |
| F-30 | Circuit breaker per destination URL | N consecutive failures → pause 30s |
| F-31 | Rate limiting per destination URL | Configurable RPM, burst |
| F-32 | Delivery idempotency via idempotency-key header | Per-attempt unique key |
| F-33 | Configurable concurrency per destination | Don't hammer slow endpoints |

### 4.6 Dead Letter Queue (`P1`)

| ID | Requirement | Notes |
|---|---|---|
| F-34 | Exhausted retries → move event to DLQ | |
| F-35 | DLQ stores: original payload, transform applied, failure reason, all attempt logs | |
| F-36 | Manual replay: single event, or batch | Events go back to delivery queue |
| F-37 | Auto-expire DLQ events after configurable TTL | Default 30 days |
| F-38 | Webhook notification on DLQ event | Optional, alertops channel |

### 4.7 Observability & Audit (`P1`)

| ID | Requirement | Notes |
|---|---|---|
| F-39 | Log every delivery attempt | Request/response bodies, status, error, duration |
| F-40 | Dashboard: success rate, latency (P50/P95/P99), volume | Time-series charts |
| F-41 | Dashboard: failure breakdown by destination | 4xx vs 5xx vs timeout vs connection error |
| F-42 | Event search: by endpoint, route, destination, status | Full-text search over payload |
| F-43 | Event detail view: full timeline | Received → transformed → each delivery attempt |
| F-44 | Export logs as JSON/CSV | For external analysis |

### 4.8 API & Dashboard (`P2`)

| ID | Requirement | Notes |
|---|---|---|
| F-45 | REST API for all management operations | Create/read/update/delete endpoints, routes |
| F-46 | Web-based dashboard for non-technical users | React SPA |
| F-47 | Dashboard: create/edit endpoints and routes | Form or JSON editor |
| F-48 | Dashboard: view recent events in real-time | Polling or SSE/WebSocket |
| F-49 | Dashboard: DLQ browser + replay action | One-click replay |

---

## 5. Non-Functional Requirements

### 5.1 Performance

| ID | Requirement | Target |
|---|---|---|
| NF-01 | Ingestion throughput | 1000 req/s per instance |
| NF-02 | P99 ingestion latency (return 202) | < 50ms |
| NF-03 | P99 delivery latency (first attempt) | < 500ms (excluding network) |
| NF-04 | Concurrent deliveries per instance | 500 |
| NF-05 | Supports horizontal scaling | Stateless ingestion + Kafka consumer groups |

### 5.2 Reliability

| ID | Requirement | Notes |
|---|---|---|
| NF-06 | No event loss on service restart | Kafka consumer offset + checkpointing |
| NF-07 | Graceful degradation on downstream failures | Circuit breaker prevents cascading |
| NF-08 | Graceful shutdown | Finish in-flight deliveries, flush pending logs |
| NF-09 | Data durability | PostgreSQL + Kafka with replication |

### 5.3 Security

| ID | Requirement | Notes |
|---|---|---|
| NF-10 | HMAC signature verification for incoming webhooks | Configurable algorithm |
| NF-11 | TLS for all ingress endpoints | Mandatory |
| NF-12 | Secrets encrypted at rest (DB) | HMAC secrets, route auth headers |
| NF-13 | Transform sandboxing | No file system, no network, no imports |
| NF-14 | Rate limiting to prevent abuse | Global + per-endpoint |

### 5.4 Operations

| ID | Requirement | Notes |
|---|---|---|
| NF-15 | Single-command deploy via Docker Compose | |
| NF-16 | Health check endpoint (`/health`) | Returns DB + Kafka + Redis connectivity |
| NF-17 | Prometheus metrics endpoint (`/metrics`) | Request rate, delivery rate, error rate, queue depth |
| NF-18 | Structured JSON logging | For ingestion into ELK/Datadog/Grafana |
| NF-19 | Configuration via environment variables | DB URL, Kafka brokers, Redis URL, listen port |

---

## 6. System Architecture

### 6.1 High-Level Architecture

```
                        ┌──────────┐
                        │  Client   │
                        │  (React)  │
                        └────┬─────┘
                             │ REST API
                             ▼
┌──────────────────────────────────────────────┐
│              FastAPI Application              │
│                                               │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ Webhook  │  │  Admin   │  │  Metrics   │ │
│  │ Receiver │  │  API     │  │  Endpoint  │ │
│  └────┬─────┘  └────┬─────┘  └────────────┘ │
└───────┼──────────────┼────────────────────────┘
        │              │
        ▼              ▼
   ┌──────────┐   ┌──────────┐
   │  Kafka   │   │PostgreSQL│
   │raw-events│   │ (config) │
   │  topic   │   │ (audit)  │
   └────┬─────┘   └──────────┘
        │
        ▼
┌───────────────────────┐
│ Transform Consumer    │
│ (reads raw-events,    │
│  writes transformed)  │
└──────────┬────────────┘
           │
           ▼
   ┌───────────────────┐
   │transformed-events │
   │ Kafka topic       │
   └──────────┬────────┘
              │
              ▼
┌───────────────────────┐
│ Delivery Consumer     │
│ (makes HTTP calls,    │
│  retries, DLQ)        │
└───────┬───────────────┘
        │
        ├──▶ Destination A
        ├──▶ Destination B
        └──▶ Destination C

┌───────────────────────┐
│ Redis                 │
│ - Idempotency keys    │
│ - Rate limiter        │
│ - Circuit breaker     │
└───────────────────────┘
```

### 6.2 Data Flow (End-to-End)

```
1. Stripe POSTs to /hooks/stripe_endpoint_1
   └─▶ FastAPI verifies HMAC, checks idempotency
      └─▶ Publishes to Kafka topic "raw-events"
         └─▶ Returns 202 to Stripe

2. Transform Consumer picks up the event
   ├─▶ Loads endpoint config from PostgreSQL
   ├─▶ For each route, applies transform rule
   │    ├─▶ Route A: JMESPath expression → new JSON
   │    ├─▶ Route B: template string → Slack message
   │    └─▶ Route C: pass-through (no transform)
   └─▶ Publishes each result to "transformed-events"

3. Delivery Consumer picks up a transformed event
   ├─▶ Makes HTTP POST to destination URL
   ├─▶ On success: log audit record to PostgreSQL
   ├─▶ On transient failure: retry with backoff
   │    └─▶ All retries exhausted: move to DLQ topic
   └─▶ On 4xx failure: log + don't retry (config error)
```

### 6.3 Kafka Topic Design

| Topic | Partitions | Retention | Purpose |
|---|---|---|---|
| `raw-events` | 3 | 7 days | Raw incoming webhook payloads |
| `transformed-events` | 6 | 7 days | Pre-transform per-route payloads |
| `dead-letter` | 1 | 30 days | Events that exhausted retries |
| `delivery-attempts` | 3 | 7 days | Audit log of every HTTP attempt |

### 6.4 Database Schema

```sql
-- Endpoints: each incoming webhook URL
CREATE TABLE endpoints (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    secret TEXT NOT NULL,           -- HMAC secret (encrypted at rest)
    is_active BOOLEAN DEFAULT true,
    ip_allowlist TEXT[],            -- CIDR notation
    rate_limit_rps INT DEFAULT 100,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Routes: where to deliver and how to transform
CREATE TABLE routes (
    id UUID PRIMARY KEY,
    endpoint_id UUID REFERENCES endpoints(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    method TEXT DEFAULT 'POST',
    headers JSONB DEFAULT '{}',     -- Static headers
    transform_rule JSONB,           -- Transform expression
    timeout_ms INT DEFAULT 10000,
    max_retries INT DEFAULT 5,
    retry_backoff_ms INT DEFAULT 1000,
    rate_limit_rpm INT DEFAULT 60,
    circuit_breaker_threshold INT DEFAULT 10,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Events: every webhook received
CREATE TABLE events (
    id UUID PRIMARY KEY,
    endpoint_id UUID REFERENCES endpoints(id),
    idempotency_key TEXT,
    source_ip TEXT,
    request_method TEXT,
    request_headers JSONB,
    request_body JSONB,
    received_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_events_endpoint_id ON events(endpoint_id);
CREATE INDEX idx_events_received_at ON events(received_at);
CREATE UNIQUE INDEX idx_events_idempotency ON events(endpoint_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Delivery attempts: every HTTP call made
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

CREATE INDEX idx_delivery_attempts_event ON delivery_attempts(event_id);
CREATE INDEX idx_delivery_attempts_status ON delivery_attempts(response_status);
```

---

## 7. API Design

### 7.1 Management API

```
POST   /api/endpoints              # Create endpoint
GET    /api/endpoints              # List all endpoints
GET    /api/endpoints/:id          # Get endpoint details
PUT    /api/endpoints/:id          # Update endpoint
DELETE /api/endpoints/:id          # Delete endpoint
POST   /api/endpoints/:id/rotate   # Rotate HMAC secret

POST   /api/endpoints/:id/routes              # Add route
GET    /api/endpoints/:id/routes              # List routes
PUT    /api/routes/:id                        # Update route
DELETE /api/routes/:id                        # Delete route

GET    /api/events?endpoint_id=&status=&from=&to=  # Search events
GET    /api/events/:id                        # Event detail + delivery attempts
POST   /api/events/:id/replay                 # Replay event

GET    /api/dead-letter                       # List DLQ events
POST   /api/dead-letter/:id/replay            # Replay DLQ event
POST   /api/dead-letter/replay-batch          # Replay all/multiple

GET    /api/stats?endpoint_id=&from=&to=      # Aggregated stats
```

### 7.2 Webhook Ingestion

```
POST /hooks/{endpoint_id}
Headers:
  X-Hub-Signature-256: sha256=<hmac>
  Idempotency-Key: <uuid>          (optional)
  Content-Type: application/json   (or others)
Body: <any valid payload>

Response 202:
{
  "status": "accepted",
  "event_id": "uuid",
  "idempotency_result": "new" | "duplicate"
}
```

---

## 8. UI / Dashboard Mockups (Text)

### 8.1 Dashboard Page

```
┌──────────────────────────────────────────────────────┐
│  Webhook Relay                         [New Endpoint] │
├──────────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
│  │ 1,234   │  │ 98.5%   │  │ 42ms    │  │ 3       │ │
│  │ Events  │  │ Success │  │ P50 Lat │  │ DLQ     │ │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘ │
│                                                       │
│  ┌─── Success Rate (Last 24h) ────────────────────┐  │
│  │  ████████████████████████████████████░░ 98.5%  │  │
│  └────────────────────────────────────────────────┘  │
│                                                       │
│  ┌─── Endpoints ──────────────────────────────────┐  │
│  │  Stripe      ● Active    1,234 events  100%    │  │
│  │  GitHub      ● Active    567 events    95%     │  │
│  │  Slack       ○ Inactive  0 events      -      │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### 8.2 Endpoint Detail

```
┌──────────────────────────────────────────────────────┐
│  ← Endpoints  /  Stripe                              │
├──────────────────────────────────────────────────────┤
│  Ingest URL:  https://relay.myapp.com/hooks/abc123   │
│  Secret:      whsec_****************************abcd  │
│  Status:      ● Active                    [Toggle]   │
│                                                       │
│  ┌─── Routes ──────────────────────────[+ Add Route] │
│  │  Slack Alert    ▶ https://slack.com/api/...       │
│  │  Analytics      ▶ https://analytics.myapp.com/... │
│  │  Email Receipt  ▶ https://email.myapp.com/...     │
│  └───────────────────────────────────────────────────┘
│                                                       │
│  ┌─── Recent Events ────────────────────────────────┐  │
│  │  cs_abc123    2 min ago   All delivered    ✓     │  │
│  │  cs_def456    5 min ago   1/3 delivered    ⚠     │  │
│  │  cs_ghi789    10 min ago  Failed           ✗     │  │
│  └──────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### 8.3 Transform Config (JSONata Editor)

```
┌─── Edit Transform: Slack Alert ─────────────────────┐
│                                                       │
│  Input Preview:                                       │
│  {                                                     │
│    "type": "checkout.session.completed",              │
│    "data": { "customer_email": "alice@a.co", ... }    │
│  }                                                     │
│                                                       │
│  Transform Rule (JSONata):                             │
│  ┌─────────────────────────────────────────────────┐  │
│  │ {                                               │  │
│  │   "text": data.customer_email & " paid $" &     │  │
│  │           (data.amount_total / 100)             │  │
│  │ }                                               │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  Output Preview:                                       │
│  { "text": "alice@a.co paid $29.99" }                 │
│                                                       │
│                                   [Test] [Save]       │
└──────────────────────────────────────────────────────┘
```

---

## 9. Implementation Phases

### Phase 1: Core Engine (Weeks 1-3)

**Goal:** Working ingestion → transform → delivery loop with PostgreSQL, no Kafka.

- [ ] FastAPI project structure, config, health endpoint
- [ ] PostgreSQL models + migrations (endpoints, routes, events, delivery_attempts)
- [ ] CRUD API for endpoints and routes
- [ ] Webhook ingestion handler (POST /hooks/{id})
- [ ] HMAC signature verification
- [ ] Basic transform engine (template strings + pass-through)
- [ ] Async delivery worker (single consumer)
- [ ] Retry with exponential backoff
- [ ] Admin API: list events + delivery attempts
- [ ] Basic React dashboard: endpoint list, event list

**Deliverable:** You can send a webhook → it transforms → delivers to URL. All logs visible in DB + dashboard.

### Phase 2: Kafka & Scalability (Week 4)

**Goal:** Decouple components with Kafka for reliability and horizontal scaling.

- [ ] Kafka setup (Docker Compose)
- [ ] Producer: publish raw events to `raw-events` topic
- [ ] Transform consumer: read raw-events, apply transforms, publish to `transformed-events`
- [ ] Delivery consumer: read transformed-events, make HTTP calls
- [ ] Consumer group config for horizontal scaling
- [ ] Idempotency key dedup (Redis)
- [ ] Rate limiting per endpoint (Redis)

**Deliverable:** System is decoupled. You can run multiple consumer instances. Events survive restarts.

### Phase 3: Production Features (Week 5-6)

**Goal:** Make it robust and observable.

- [ ] Circuit breaker per destination (Redis-backed)
- [ ] Dead letter queue (Kafka topic + API)
- [ ] DLQ replay (single + batch)
- [ ] Transform validation at config time
- [ ] IP allowlisting
- [ ] Prometheus metrics endpoint
- [ ] Structured JSON logging
- [ ] Dashboard: charts (success rate, latency, volume)
- [ ] Dashboard: DLQ browser + replay
- [ ] Dashboard: event detail timeline view
- [ ] Endpoint toggle (active/inactive)
- [ ] Secret rotation

**Deliverable:** Production-ready for small teams.

### Phase 4: Polish & DX (Week 7-8)

**Goal:** Make it easy to deploy and use.

- [ ] Docker Compose with all services
- [ ] Environment variable configuration
- [ ] CLI seed command (create demo endpoint + routes)
- [ ] .env.example with all config options
- [ ] Export events as JSON/CSV
- [ ] Real-time event stream via SSE
- [ ] Webhook notification on DLQ event
- [ ] Readme with architecture diagram + quickstart
- [ ] Load testing script (locust/k6)

**Deliverable:** Anyone can `docker compose up` and have a working webhook relay in 2 minutes.

---

## 10. Tech Stack Justification

| Component | Choice | Why |
|---|---|---|
| **API Framework** | FastAPI | Async-native, Pydantic validation, auto OpenAPI docs, high perf |
| **Queue** | Kafka | You already know it. Durable, replayable, consumer groups for scaling |
| **Database** | PostgreSQL | Reliable, JSONB for flexible payload storage, mature ORMs |
| **Cache** | Redis | Perfect for idempotency TTL, rate limiter counters, circuit breaker state |
| **Transform Engine** | JSONata | Designed for JSON transforms, safer than eval, JS/Python libs |
| **Frontend** | React + Vite + Tailwind | Fast dev cycle, component libraries available |
| **HTTP Client** | httpx | Async, connection pooling, timeout support |
| **Container** | Docker Compose | Single-command deploy, standard for self-hosted tools |
| **Metrics** | Prometheus + Grafana (optional) | Industry standard, auto-discover via /metrics |

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Transform sandbox escape | Security breach | Use JSONata (no system access), no eval(), limit recursion depth |
| Downstream API slow/crashes | Delivery backlog | Circuit breaker, per-destination concurrency limits, max timeout |
| Kafka broker goes down | Event loss | Replication factor 3, acks=all producer config |
| PostgreSQL becomes bottleneck | Slow query / insert | Partition events table by date, batch inserts for audit logs |
| User misconfigures route | 4xx errors on every attempt | Config-time validation, "test transform" button, preview output |
| Startup deploys and forgets | Unused / broken pipelines | Dashboard with endpoint health summary, alert on delivery failures |

---

## 12. Success Metrics

| Metric | Target | Why |
|---|---|---|
| Events processed without loss | 99.999% | Core reliability promise |
| P50 delivery latency | < 200ms | Fast enough for real-time notifications |
| Time to get first webhook delivered | < 5 minutes | From `docker compose up` to first successful relay |
| Dashboard load time | < 2s | Snappy UX |
| Endpoint config time | < 30s | Quick setup for a simple route |

---

## 13. Open Questions

- [ ] Should we support webhook payload validation against a schema? (JSON Schema)
- [ ] Should we support webhook filtering (only process events matching a condition)?
- [ ] Should we support batching (aggregate multiple events into one delivery)?
- [ ] Should we support custom auth methods (Basic, Bearer, API Key header) beyond static headers?
- [ ] Should we support non-HTTP destinations (WebSocket, SQS, Pub/Sub)?
- [ ] What's the license? MIT or AGPL?
- [ ] Should we provide a hosted cloud version too? (future)

---

## 14. Appendix: Comparison with Alternatives

| Feature | This Service | Zapier | Pipedream | Custom Build |
|---|---|---|---|---|
| Self-hosted | ✅ | ❌ | ❌ | ✅ |
| Transform engine | ✅ | ✅ | ✅ | You build it |
| Retry + backoff | ✅ | ✅ | ✅ | You build it |
| Dead letter queue | ✅ | Limited | ✅ | You build it |
| Audit log | ✅ | Limited | ✅ | You build it |
| Horizontal scaling | ✅ (Kafka) | N/A (hosted) | N/A (hosted) | You build it |
| Cost at 100k events/mo | Server cost only | ~$100/mo | ~$50/mo | Dev time |
| Custom destinations | ✅ | Limited (apps) | Limited (apps) | ✅ |
| Data stays in network | ✅ | ❌ | ❌ | ✅ |
| Setup time | 5 min | 30 min | 30 min | Weeks |

---

*This is a living document. Update as the project evolves.*
