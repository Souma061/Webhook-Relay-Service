# Contributing

Thanks for your interest in contributing to Webhook Relay!

## Getting Started

1. Fork the repo
2. Clone your fork: `git clone git@github.com:YOUR_USERNAME/Webhook-Relay-Service.git`
3. Copy the config: `cp .env.example .env` and fill in secrets
4. Start the dev stack: `docker compose -f docker-compose.dev.yml up -d`
5. Run tests: `pytest tests/unit/ -q --tb=short`

## Development

- All source code is in `app/` and `workers/`
- The dev compose mounts these directories for hot-reload
- Follow existing code style (no docstrings/comments unless necessary)
- Add tests for new features

## Before Submitting

- Run `pytest tests/unit/ -q --tb=short` — all tests must pass
- Run `pytest e2e_test.py -q --tb=short` — E2E tests against the running stack

## Project Structure

```
app/              — FastAPI application
  gateway/        — Webhook ingress
  transform/      — Transform pipeline engine
  delivery/       — Delivery with CB, rate limiter, backoff, DLQ
  core/           — DB, Redis, Kafka, config
  models/         — SQLAlchemy ORM models
  schemas/        — Pydantic schemas
  middleware/     — RBAC middleware
workers/          — Kafka consumer workers
dashboard/        — React dashboard (Vdoite)
tests/            — Unit and E2E tests
```

## Questions?

Open a GitHub Discussion or issue.
