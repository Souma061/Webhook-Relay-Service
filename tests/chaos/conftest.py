"""Shared fixtures for chaos tests — load patterns, concurrency, resilience."""

import asyncio
import hashlib
import hmac
import json
import uuid
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

import app.core.redis as core_redis
from app.core.rate_limiter import SlidingWindowRateLimiter


# ── HMAC signing utility ───────────────────────────────────────────────────────

def sign_payload(secret: str, payload: bytes) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


# ── Mock Redis pipeline (for unit chaos tests) ─────────────────────────────────

@pytest.fixture
def mock_redis_pipeline():
    pipeline = MagicMock()
    pipeline.zremrangebyscore = MagicMock()
    pipeline.zcard = MagicMock()
    pipeline.zadd = MagicMock()
    pipeline.expire = MagicMock()
    pipeline.execute = AsyncMock()

    redis = MagicMock()
    redis.pipeline.return_value = pipeline

    core_redis.redis_client = redis
    yield redis, pipeline
    core_redis.redis_client = None


@pytest.fixture
def limiter():
    rl = SlidingWindowRateLimiter()
    rl.max_rpm = 60
    return rl


# ── Inject a stopped Kafka producer (no-ops on send) ───────────────────────────

@pytest.fixture(autouse=True)
def kafka_stopped():
    import app.core.kafka as k
    old = k._producer
    k._producer = None
    yield
    k._producer = old


# ── Live-endpoint fixtures: create endpoint + route on demand ──────────────────

@pytest_asyncio.fixture(scope="module")
async def live_endpoint(session_client, auth_headers, workspace_id):
    """Create an endpoint with a known HMAC secret, yield its id + secret."""
    secret = "chaos-test-secret-" + uuid.uuid4().hex[:16]
    resp = await session_client.post(
        f"/api/workspaces/{workspace_id}/endpoints",
        json={"name": "Chaos Endpoint", "hmac_secret": secret},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    ep = resp.json()
    return {"id": ep["id"], "secret": secret}


@pytest_asyncio.fixture(scope="module")
async def live_route(live_endpoint, session_client, auth_headers, workspace_id):
    """Add a route pointing at the collector (port 9999)."""
    resp = await session_client.post(
        f"/api/workspaces/{workspace_id}/endpoints/{live_endpoint['id']}/routes",
        json={
            "url": "http://host.docker.internal:9999",
            "name": "chaos-collector",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Concurrent event sender ────────────────────────────────────────────────────

class ConcurrentEventSender:
    """Fire N events concurrently to the same endpoint, return results."""

    def __init__(self, client: AsyncClient, endpoint_id: str, secret: str):
        self.client = client
        self.endpoint_id = endpoint_id
        self.secret = secret
        self.base_url = "http://test"

    async def send_one(self, seq: int, idem_key: str | None = None) -> tuple[int, int, dict]:
        payload = {
            "seq": seq,
            "event": "chaos.test",
            "timestamp": seq,
            "data": {"nested": {"deep": True}},
        }
        raw = json.dumps(payload).encode()
        sig = sign_payload(self.secret, raw)
        headers = {"x-hub-signature-256": sig, "content-type": "application/json"}
        if idem_key:
            headers["x-idempotency-key"] = idem_key

        resp = await self.client.post(
            f"/hooks/{self.endpoint_id}",
            content=raw,
            headers=headers,
        )
        return seq, resp.status_code, resp.json()

    async def flood(self, count: int, concurrency: int = 50) -> list[tuple[int, int, dict]]:
        sem = asyncio.Semaphore(concurrency)

        async def _limited(seq):
            async with sem:
                return await self.send_one(seq)

        tasks = [_limited(i) for i in range(count)]
        return await asyncio.gather(*tasks)
