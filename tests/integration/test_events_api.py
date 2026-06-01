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

ENDPOINT_PATH = "/api/workspaces/{workspace_id}/endpoints"
EVENTS_PATH = "/api/workspaces/{workspace_id}/events"
DLQ_PATH = "/api/workspaces/{workspace_id}/dlq"
KNOWN_SECRET = "int-evts-test-secret-99887766554433221100aabbccddeeff"


def make_signature(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def ingest_event(client, auth_headers, workspace_id, ep: dict, body: dict = None) -> str:
    payload = (b'{"event":"test"}' if body is None
               else __import__("json").dumps(body).encode())
    sig = make_signature(KNOWN_SECRET, payload)
    resp = await client.post(
        f"/hooks/{ep['id']}",
        content=payload,
        headers={"x-hub-signature-256": sig},
    )
    assert resp.status_code == 202
    return resp.json()["event_id"]


async def create_endpoint(
    client, auth_headers, workspace_id, name: str = "Events EP",
) -> dict:
    resp = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": name, "hmac_secret": KNOWN_SECRET},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


class TestListEvents:
    async def test_list_events_returns_200_array(self, client, auth_headers, workspace_id):
        resp = await client.get(
            EVENTS_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_newly_ingested_event_appears_in_list(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "List Events EP")
        event_id = await ingest_event(client, auth_headers, workspace_id, ep)

        resp = await client.get(
            EVENTS_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        ids = [e["id"] for e in resp.json()]
        assert event_id in ids

    async def test_filter_by_endpoint_id(self, client, auth_headers, workspace_id):
        ep1 = await create_endpoint(client, auth_headers, workspace_id, "EP Filter 1")
        ep2 = await create_endpoint(client, auth_headers, workspace_id, "EP Filter 2")
        eid1 = await ingest_event(client, auth_headers, workspace_id, ep1)
        eid2 = await ingest_event(client, auth_headers, workspace_id, ep2)

        resp = await client.get(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}?endpoint_id={ep1['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        ids = [e["id"] for e in resp.json()]
        assert eid1 in ids
        assert eid2 not in ids

    async def test_filter_invalid_endpoint_uuid_returns_400(self, client, auth_headers, workspace_id):
        resp = await client.get(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}?endpoint_id=not-a-uuid",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_pagination_limit_and_offset(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Paginate EP")
        for i in range(5):
            await ingest_event(client, auth_headers, workspace_id, ep, {"i": i})

        events_path = EVENTS_PATH.format(workspace_id=workspace_id)
        resp_limit = await client.get(
            f"{events_path}?endpoint_id={ep['id']}&limit=2",
            headers=auth_headers,
        )
        assert len(resp_limit.json()) == 2

        resp_offset = await client.get(
            f"{events_path}?endpoint_id={ep['id']}&limit=10&offset=3",
            headers=auth_headers,
        )
        assert len(resp_offset.json()) == 2


class TestGetEvent:
    async def test_get_existing_event(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Get Event EP")
        event_id = await ingest_event(client, auth_headers, workspace_id, ep)

        resp = await client.get(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/{event_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == event_id

    async def test_get_nonexistent_event_returns_404(self, client, auth_headers, workspace_id):
        resp = await client.get(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/00000000-0000-0000-0000-000000000099",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_get_invalid_uuid_returns_400(self, client, auth_headers, workspace_id):
        resp = await client.get(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/not-a-uuid",
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestDeliveryAttempts:
    async def test_attempts_empty_for_fresh_event(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Attempts EP")
        event_id = await ingest_event(client, auth_headers, workspace_id, ep)

        resp = await client.get(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/{event_id}/attempts",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_attempts_recorded_for_event(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Attempts Recorded EP")
        event_id = await ingest_event(client, auth_headers, workspace_id, ep)

        async with async_session_factory() as db:
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

        resp = await client.get(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/{event_id}/attempts",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        a = resp.json()[0]
        assert a["attempt_number"] == 0
        assert a["response_status"] == 200
        assert a["duration_ms"] == 42


class TestEventReplay:
    async def test_replay_returns_202(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Replay EP")
        event_id = await ingest_event(client, auth_headers, workspace_id, ep)
        core_kafka._producer.send_and_wait.reset_mock()

        resp = await client.post(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/{event_id}/replay",
            headers=auth_headers,
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "replaying"
        assert resp.json()["event_id"] == event_id

    async def test_replay_publishes_to_kafka_with_is_replay_flag(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Replay Kafka EP")
        event_id = await ingest_event(client, auth_headers, workspace_id, ep)
        import asyncio
        await asyncio.sleep(0)
        core_kafka._producer.send_and_wait.reset_mock()

        await client.post(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/{event_id}/replay",
            headers=auth_headers,
        )

        core_kafka._producer.send_and_wait.assert_called_once()
        call_args = core_kafka._producer.send_and_wait.call_args
        msg = call_args[1]["value"] if call_args[1] else call_args[0][1]
        assert msg["event_id"] == event_id
        assert msg.get("is_replay") is True

    async def test_replay_nonexistent_event_returns_404(self, client, auth_headers, workspace_id):
        resp = await client.post(
            f"{EVENTS_PATH.format(workspace_id=workspace_id)}/00000000-0000-0000-0000-000000000099/replay",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestDLQ:
    async def _create_failing_event(self, client, auth_headers, workspace_id) -> str:
        ep = await create_endpoint(
            client, auth_headers, workspace_id,
            f"DLQ EP {uuid.uuid4().hex[:6]}",
        )
        event_id = await ingest_event(client, auth_headers, workspace_id, ep)

        async with async_session_factory() as db:
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

    async def test_dlq_list_returns_200_array(self, client, auth_headers, workspace_id):
        resp = await client.get(
            DLQ_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_failed_event_appears_in_dlq(self, client, auth_headers, workspace_id):
        event_id = await self._create_failing_event(client, auth_headers, workspace_id)
        resp = await client.get(
            DLQ_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        ids = [e["event_id"] for e in resp.json()]
        assert event_id in ids

    async def test_discard_event(self, client, auth_headers, workspace_id):
        event_id = await self._create_failing_event(client, auth_headers, workspace_id)
        resp = await client.post(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}/{event_id}/discard",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "discarded"

    async def test_discarded_event_hidden_from_default_dlq(self, client, auth_headers, workspace_id):
        event_id = await self._create_failing_event(client, auth_headers, workspace_id)
        await client.post(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}/{event_id}/discard",
            headers=auth_headers,
        )

        resp = await client.get(
            DLQ_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        ids = [e["event_id"] for e in resp.json()]
        assert event_id not in ids

    async def test_discarded_event_visible_with_include_discarded(self, client, auth_headers, workspace_id):
        event_id = await self._create_failing_event(client, auth_headers, workspace_id)
        await client.post(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}/{event_id}/discard",
            headers=auth_headers,
        )

        resp = await client.get(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}?include_discarded=true",
            headers=auth_headers,
        )
        ids = [e["event_id"] for e in resp.json()]
        assert event_id in ids

    async def test_restore_discarded_event(self, client, auth_headers, workspace_id):
        event_id = await self._create_failing_event(client, auth_headers, workspace_id)
        await client.post(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}/{event_id}/discard",
            headers=auth_headers,
        )
        resp = await client.post(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}/{event_id}/restore",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

        dlq_resp = await client.get(
            DLQ_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        ids = [e["event_id"] for e in dlq_resp.json()]
        assert event_id in ids

    async def test_discard_already_discarded_is_idempotent(self, client, auth_headers, workspace_id):
        event_id = await self._create_failing_event(client, auth_headers, workspace_id)
        await client.post(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}/{event_id}/discard",
            headers=auth_headers,
        )
        resp = await client.post(
            f"{DLQ_PATH.format(workspace_id=workspace_id)}/{event_id}/discard",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_discarded"
