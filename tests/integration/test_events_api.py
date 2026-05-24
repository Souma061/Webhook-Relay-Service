"""
Integration tests for the Events API and Dead Letter Queue (DLQ) endpoints.

Covers: list events, get event, list delivery attempts, event replay via Kafka,
DLQ listing, discard, restore operations.
Requires PostgreSQL + Redis (conftest.py). Kafka is mocked.
"""
import hashlib
import hmac
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from httpx import AsyncClient

import app.core.kafka as core_kafka
from app.core.database import async_session_factory
from app.models.event import Event
from app.models.route import Route
from app.models.delivery_attempt import DeliveryAttempt


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_signature(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def ingest_event(client: AsyncClient, ep: dict, body: dict = None) -> str:
    """Send a webhook and return the event_id."""
    payload = (b'{"event":"test"}' if body is None
               else __import__("json").dumps(body).encode())
    sig = make_signature(ep["hmac_secret"], payload)
    resp = await client.post(
        f"/hooks/{ep['id']}",
        content=payload,
        headers={"x-hub-signature-256": sig},
    )
    assert resp.status_code == 202
    return resp.json()["event_id"]


async def create_endpoint(client: AsyncClient, name: str = "Events EP") -> dict:
    resp = await client.post("/api/endpoints/", json={"name": name})
    assert resp.status_code == 201
    return resp.json()


# ── Events List ────────────────────────────────────────────────────────────────

class TestListEvents:
    async def test_list_events_returns_200_array(self, client: AsyncClient):
        resp = await client.get("/api/events/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_newly_ingested_event_appears_in_list(self, client: AsyncClient):
        ep = await create_endpoint(client, "List Events EP")
        event_id = await ingest_event(client, ep)

        resp = await client.get("/api/events/")
        ids = [e["id"] for e in resp.json()]
        assert event_id in ids

    async def test_filter_by_endpoint_id(self, client: AsyncClient):
        ep1 = await create_endpoint(client, "EP Filter 1")
        ep2 = await create_endpoint(client, "EP Filter 2")
        eid1 = await ingest_event(client, ep1)
        eid2 = await ingest_event(client, ep2)

        resp = await client.get(f"/api/events/?endpoint_id={ep1['id']}")
        assert resp.status_code == 200
        ids = [e["id"] for e in resp.json()]
        assert eid1 in ids
        assert eid2 not in ids

    async def test_filter_invalid_endpoint_uuid_returns_400(self, client: AsyncClient):
        resp = await client.get("/api/events/?endpoint_id=not-a-uuid")
        assert resp.status_code == 400

    async def test_pagination_limit_and_offset(self, client: AsyncClient):
        ep = await create_endpoint(client, "Paginate EP")
        for i in range(5):
            await ingest_event(client, ep, {"i": i})

        resp_limit = await client.get(f"/api/events/?endpoint_id={ep['id']}&limit=2")
        assert len(resp_limit.json()) == 2

        resp_offset = await client.get(f"/api/events/?endpoint_id={ep['id']}&limit=10&offset=3")
        assert len(resp_offset.json()) == 2  # 5 total - 3 offset = 2


# ── Get Single Event ───────────────────────────────────────────────────────────

class TestGetEvent:
    async def test_get_existing_event(self, client: AsyncClient):
        ep = await create_endpoint(client, "Get Event EP")
        event_id = await ingest_event(client, ep)

        resp = await client.get(f"/api/events/{event_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == event_id

    async def test_get_nonexistent_event_returns_404(self, client: AsyncClient):
        resp = await client.get("/api/events/00000000-0000-0000-0000-000000000099")
        assert resp.status_code == 404

    async def test_get_invalid_uuid_returns_400(self, client: AsyncClient):
        resp = await client.get("/api/events/not-a-uuid")
        assert resp.status_code == 400


# ── Delivery Attempts ─────────────────────────────────────────────────────────

class TestDeliveryAttempts:
    async def test_attempts_empty_for_fresh_event(self, client: AsyncClient):
        ep = await create_endpoint(client, "Attempts EP")
        event_id = await ingest_event(client, ep)

        resp = await client.get(f"/api/events/{event_id}/attempts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_attempts_recorded_for_event(self, client: AsyncClient):
        """Insert a DeliveryAttempt directly in DB and verify it appears via API."""
        ep = await create_endpoint(client, "Attempts Recorded EP")
        event_id = await ingest_event(client, ep)

        async with async_session_factory() as db:
            # Must create a real route — route_id FK is non-nullable
            route = Route(
                endpoint_id=uuid.UUID(ep["id"]),
                name="Attempt Route",
                url="http://dest.example.com",
            )
            db.add(route)
            await db.commit()
            await db.refresh(route)

            attempt = DeliveryAttempt(
                event_id=uuid.UUID(event_id),
                route_id=route.id,
                attempt_number=0,
                request_url="http://dest.example.com",
                request_body={"event": "test"},
                response_status=200,
                response_body="OK",
                error=None,
                duration_ms=42,
            )
            db.add(attempt)
            await db.commit()

        resp = await client.get(f"/api/events/{event_id}/attempts")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        a = resp.json()[0]
        assert a["attempt_number"] == 0
        assert a["response_status"] == 200
        assert a["duration_ms"] == 42


# ── Event Replay ──────────────────────────────────────────────────────────────

class TestEventReplay:
    async def test_replay_returns_202(self, client: AsyncClient):
        ep = await create_endpoint(client, "Replay EP")
        event_id = await ingest_event(client, ep)
        core_kafka._producer.send_and_wait.reset_mock()

        resp = await client.post(f"/api/events/{event_id}/replay")
        assert resp.status_code == 202
        assert resp.json()["status"] == "replaying"
        assert resp.json()["event_id"] == event_id

    async def test_replay_publishes_to_kafka_with_is_replay_flag(self, client: AsyncClient):
        ep = await create_endpoint(client, "Replay Kafka EP")
        event_id = await ingest_event(client, ep)
        core_kafka._producer.send_and_wait.reset_mock()

        await client.post(f"/api/events/{event_id}/replay")

        core_kafka._producer.send_and_wait.assert_called_once()
        call_args = core_kafka._producer.send_and_wait.call_args
        msg = call_args[1]["value"] if call_args[1] else call_args[0][1]
        assert msg["event_id"] == event_id
        assert msg.get("is_replay") is True

    async def test_replay_nonexistent_event_returns_404(self, client: AsyncClient):
        resp = await client.post("/api/events/00000000-0000-0000-0000-000000000099/replay")
        assert resp.status_code == 404


# ── Dead Letter Queue ─────────────────────────────────────────────────────────

class TestDLQ:
    async def _create_failing_event(self, client: AsyncClient) -> str:
        """Create an event with a failed delivery attempt (simulates a DLQ item)."""
        ep = await create_endpoint(client, f"DLQ EP {uuid.uuid4().hex[:6]}")
        event_id = await ingest_event(client, ep)

        async with async_session_factory() as db:
            # Create a real route so the FK on delivery_attempts.route_id is satisfied
            route = Route(
                endpoint_id=uuid.UUID(ep["id"]),
                name="DLQ Test Route",
                url="http://failing.example.com",
            )
            db.add(route)
            await db.commit()
            await db.refresh(route)

            attempt = DeliveryAttempt(
                event_id=uuid.UUID(event_id),
                route_id=route.id,
                attempt_number=0,
                request_url="http://failing.example.com",
                request_body={"event": "test"},
                response_status=503,
                response_body="Service Unavailable",
                error="server_error",
                duration_ms=100,
            )
            db.add(attempt)
            await db.commit()

        return event_id

    async def test_dlq_list_returns_200_array(self, client: AsyncClient):
        resp = await client.get("/api/dlq/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_failed_event_appears_in_dlq(self, client: AsyncClient):
        event_id = await self._create_failing_event(client)
        resp = await client.get("/api/dlq/")
        ids = [e["event_id"] for e in resp.json()]
        assert event_id in ids

    async def test_discard_event(self, client: AsyncClient):
        event_id = await self._create_failing_event(client)

        resp = await client.post(f"/api/dlq/{event_id}/discard")
        assert resp.status_code == 200
        assert resp.json()["status"] == "discarded"

    async def test_discarded_event_hidden_from_default_dlq(self, client: AsyncClient):
        event_id = await self._create_failing_event(client)
        await client.post(f"/api/dlq/{event_id}/discard")

        resp = await client.get("/api/dlq/")
        ids = [e["event_id"] for e in resp.json()]
        assert event_id not in ids

    async def test_discarded_event_visible_with_include_discarded(self, client: AsyncClient):
        event_id = await self._create_failing_event(client)
        await client.post(f"/api/dlq/{event_id}/discard")

        resp = await client.get("/api/dlq/?include_discarded=true")
        ids = [e["event_id"] for e in resp.json()]
        assert event_id in ids

    async def test_restore_discarded_event(self, client: AsyncClient):
        event_id = await self._create_failing_event(client)
        await client.post(f"/api/dlq/{event_id}/discard")
        resp = await client.post(f"/api/dlq/{event_id}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

        # Should now appear in the default DLQ again
        dlq_resp = await client.get("/api/dlq/")
        ids = [e["event_id"] for e in dlq_resp.json()]
        assert event_id in ids

    async def test_discard_already_discarded_is_idempotent(self, client: AsyncClient):
        event_id = await self._create_failing_event(client)
        await client.post(f"/api/dlq/{event_id}/discard")
        resp = await client.post(f"/api/dlq/{event_id}/discard")
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_discarded"
