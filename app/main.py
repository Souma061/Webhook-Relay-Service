from contextlib import asynccontextmanager

from fastapi import FastAPI  # pyright: ignore[reportMissingImports]

from app.core.config import settings
from app.core.database import init_db
from app.core.redis import init_redis, close_redis
from app.gateway.handler import router as gateway_router
from app.api.endpoints import router as admin_endpoints_router
from app.api.events import router as admin_events_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_redis()
    yield
    await close_redis()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(gateway_router)
app.include_router(admin_endpoints_router)
app.include_router(admin_events_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
"""
main.py — FastAPI application entry point.

Initializes database and Redis on startup, cleans up on shutdown.
Mounts three router groups:
  - /hooks/{endpoint_id} — webhook ingestion (public)
  - /api/endpoints — admin CRUD (private)
  - /api/events — event search and replay (private)
  - /health — health check

Phase 1: single process, no Kafka. Delivery runs via background tasks.
"""
