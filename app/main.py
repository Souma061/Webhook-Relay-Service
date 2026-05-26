from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings, validate_production_settings
from app.core.database import init_db, engine
from app.core.redis import init_redis, close_redis
from app.core.kafka import init_kafka, close_kafka
from app.gateway.handler import router as gateway_router
from app.api.endpoints import router as workspace_endpoints_router
from app.api.events import router as workspace_events_router
from app.api.auth import router as auth_router
from app.api.workspaces import router as workspaces_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_settings()
    await init_db()
    await init_redis()
    await init_kafka()
    yield
    await close_kafka()
    await close_redis()
    await engine.dispose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

cors_origins = [
    origin.strip()
    for origin in settings.cors_allowed_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Hub-Signature-256", "Idempotency-Key"],
)

app.include_router(gateway_router)
app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(workspace_endpoints_router)
app.include_router(workspace_events_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
