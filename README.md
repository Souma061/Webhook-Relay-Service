# Webhook Relay

[![CI](https://github.com/Souma061/Webhook-Relay-Service/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Souma061/Webhook-Relay-Service/actions/workflows/docker-publish.yml)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/Frontend-React-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![Kafka](https://img.shields.io/badge/Broker-Kafka-231F20?style=flat&logo=apachekafka&logoColor=white)](https://kafka.apache.org)
[![Redis](https://img.shields.io/badge/Cache-Redis-DC382D?style=flat&logo=redis&logoColor=white)](https://redis.io)
[![Postgres](https://img.shields.io/badge/DB-PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A self-hostable webhook relay with ingestion, HMAC verification, idempotency, payload transformations, and resilient delivery with circuit breakers, rate limiting, and dead-letter queues.

---

## Architecture

```mermaid
sequenceDiagram
    autonumber
    actor Source as Webhook Source
    participant Gateway as Ingestion Gateway
    participant Redis as Redis
    participant Kafka as Kafka
    participant Transform as Transform Worker
    participant DB as PostgreSQL
    participant Delivery as Delivery Worker
    actor Destination as Target API

    Source->>Gateway: POST /hooks/:endpoint_id
    Gateway->>Redis: Check idempotency key
    Redis-->>Gateway: Cache match?
    Gateway->>DB: Save raw event
    Gateway->>Kafka: Produce to raw-events
    Gateway-->>Source: 202 Accepted

    Kafka->>Transform: Consume raw-events
    Transform->>DB: Fetch endpoint & routes
    Transform->>Transform: Apply JMESPath/template transforms
    Transform->>Kafka: Produce to transformed-events

    Kafka->>Delivery: Consume transformed-events
    Delivery->>Redis: Check circuit breaker & rate limits
    Delivery->>Destination: HTTP POST payload
    Destination-->>Delivery: Response
    Delivery->>DB: Log delivery attempt

    alt Retries exhausted
        Delivery->>Kafka: Produce to dead-letter topic
    end
```

---

## Features

- **Ingestion**: HMAC-SHA256 signature verification, Redis-backed idempotency, instant 202 response
- **Transform pipeline**: JMESPath expressions and template replacement per route
- **Delivery engine**: Circuit breaker, sliding-window rate limiter, exponential backoff with jitter, concurrency control (semaphore)
- **Dead-letter queue**: Exhausted retries land in DLQ topic; replay via REST API
- **RBAC**: Workspace-scoped roles (owner/member/viewer) with JWT auth
- **SSRF protection**: Blocks private IPs, localhost, credentials in URLs, non-HTTPS
- **Dashboard**: React SPA for managing endpoints, routes, events, and replays

---

## Quickstart

```bash
# Download the compose file
curl -O https://raw.githubusercontent.com/Souma061/Webhook-Relay-Service/main/docker-compose.yml

# Create config from template
cp .env.example .env
# Edit .env: generate JWT_SECRET_KEY and PASSWORD_PEPPER

# Start everything
docker compose up -d
```

| Service   | URL                       |
|-----------|---------------------------|
| Dashboard | http://localhost:8080     |
| API       | http://localhost:8000     |
| API docs  | http://localhost:8000/docs|

---

## Development

```bash
git clone https://github.com/Souma061/Webhook-Relay-Service.git
cd Webhook-Relay-Service
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d
```

The dev compose mounts source directories for hot-reload and runs the dashboard as a Vite dev server on port 5173.

---

## Structure

```
app/              — FastAPI application
  gateway/        — Webhook ingress
  transform/      — Transform pipeline engine
  delivery/       — Delivery with CB, rate limiter, backoff, DLQ
  core/           — DB, Redis, Kafka, config
  models/         — SQLAlchemy ORM models
  schemas/        — Pydantic schemas
  middleware/     — RBAC middleware
  api/            — REST endpoints
workers/          — Kafka consumer workers
dashboard/        — React dashboard (Vite + nginx)
tests/            — Unit and E2E tests
```

---

## Tech Stack

| Layer       | Technology                        |
|-------------|-----------------------------------|
| API         | FastAPI (Python 3.12+)            |
| Database    | PostgreSQL 16 + SQLAlchemy async  |
| Cache       | Redis 7                           |
| Broker      | Apache Kafka (KRaft, no ZK)       |
| Transform   | JMESPath + custom template engine |
| Frontend    | React + TypeScript + Vite + nginx |
| Container   | Docker + multi-stage builds       |
| CI/CD       | GitHub Actions (tests on push, images on tags) |

---

## Docker Images

Published to Docker Hub on every version tag:

- `souma061/webhook-relay:<version>` — API + workers (multi-arch: amd64 + arm64)
- `souma061/webhook-relay-dashboard:<version>` — React dashboard (nginx)

Trigger a release: `git tag v1.0.0 && git push origin v1.0.0`

---

## Configuration

See [`.env.example`](.env.example) for all options. Key variables:

| Variable | Default | Description |
|---|---|---|
| `RELAY_DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@postgres:5432/webhook_relay` | PostgreSQL connection |
| `RELAY_REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `RELAY_KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka broker |
| `RELAY_JWT_SECRET_KEY` | (change me) | JWT signing key |
| `RELAY_RATE_LIMIT_RPM` | `60` | Default rate limit per route |

---

## Blog

Detailed technical deep-dive covering design decisions, trade-offs, and code walkthroughs:
[Building a Production-Grade Webhook Relay System](https://www.soumabrata.me/blog.html)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
