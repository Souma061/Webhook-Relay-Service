import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from unittest.mock import AsyncMock, MagicMock

# Configure settings BEFORE importing the app
os.environ["RELAY_DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5433/webhook_relay"
os.environ["RELAY_REDIS_URL"] = "redis://localhost:6379/0"

# Mock Kafka before importing app or core components to prevent connection attempts
import app.core.kafka as core_kafka
mock_producer = MagicMock()
mock_producer.start = AsyncMock()
mock_producer.stop = AsyncMock()
mock_producer.send_and_wait = AsyncMock()

async def mock_init_kafka():
    core_kafka._producer = mock_producer
    return mock_producer

async def mock_close_kafka():
    core_kafka._producer = None

core_kafka.init_kafka = mock_init_kafka
core_kafka.close_kafka = mock_close_kafka
core_kafka._producer = mock_producer

# Import database module and patch it BEFORE importing the app or endpoints.
# This ensures that all endpoints import the patched NullPool session factory,
# avoiding 'another operation is in progress' errors across tests.
import app.core.database as core_db
core_db.engine = create_async_engine(os.environ["RELAY_DATABASE_URL"], echo=False, poolclass=NullPool)
core_db.async_session_factory = async_sessionmaker(core_db.engine, class_=AsyncSession, expire_on_commit=False)
from app.core.database import Base

from app.main import app
from app.core.redis import init_redis, close_redis

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    async with core_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with core_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_redis():
    await init_redis()
    yield
    await close_redis()


# ── Auth fixtures (session-scoped) ─────────────────────────────────────────────

_TEST_USER_EMAIL = "test-integration@webhook-relay.local"
_TEST_USER_PASSWORD = "Str0ng!TestPass"


@pytest_asyncio.fixture(scope="session")
async def session_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="session")
async def test_user(session_client):
    resp = await session_client.post("/api/auth/register", json={
        "email": _TEST_USER_EMAIL,
        "password": _TEST_USER_PASSWORD,
        "display_name": "Integration Tester",
    })
    if resp.status_code == 201:
        data = resp.json()
        return data["access_token"], data["user"]
    resp = await session_client.post("/api/auth/login", json={
        "email": _TEST_USER_EMAIL,
        "password": _TEST_USER_PASSWORD,
    })
    assert resp.status_code == 200
    data = resp.json()
    return data["access_token"], data["user"]


@pytest_asyncio.fixture(scope="session")
async def auth_headers(test_user):
    token, _ = test_user
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="session")
async def workspace_id(test_user, auth_headers, session_client):
    resp = await session_client.get("/api/workspaces/", headers=auth_headers)
    assert resp.status_code == 200
    workspaces = resp.json()
    for ws in workspaces:
        return ws["id"]
    raise RuntimeError("No workspace found for test user")


# ── Function-scoped client (for per-test isolation) ────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
