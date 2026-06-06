import hashlib
import hmac
import json
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.event import Event
from app.models.outbox import OutboxRecord

ENDPOINT_PATH = "/api/workspaces/{workspace_id}/endpoints"
KNOWN_SECRET = "int-gw-test-secret-aabbccdd00112233445566778899eeff"


def make_signature(secret: str, payload: bytes) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


async def create_endpoint(
    client: AsyncClient, auth_headers: dict, workspace_id: str, name: str = "GW Endpoint",
) -> dict:
    resp = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": name, "hmac_secret": KNOWN_SECRET},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


class TestSignatureVerification:
    async def test_missing_signature_returns_401(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Sig EP 1")
        resp = await client.post(f"/hooks/{ep['id']}", json={"data": "hello"})
        assert resp.status_code == 401
        assert "missing" in resp.json()["detail"].lower()

    async def test_wrong_signature_returns_401(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Sig EP 2")
        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=b'{"data":"hello"}',
            headers={"x-hub-signature-256": "sha256=deadbeef"},
        )
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    async def test_signature_for_wrong_secret_returns_401(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Sig EP 3")
        payload = b'{"data":"hello"}'
        wrong_sig = make_signature("wrong_secret", payload)
        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": wrong_sig},
        )
        assert resp.status_code == 401


class TestNonexistentEndpoint:
    async def test_unknown_endpoint_id_returns_404(self, client, auth_headers, workspace_id):
        payload = b'{"data":"hello"}'
        sig = make_signature("anysecret", payload)
        resp = await client.post(
            "/hooks/00000000-0000-0000-0000-000000000000",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 404

    async def test_invalid_endpoint_uuid_returns_400(self, client, auth_headers, workspace_id):
        payload = b"{}"
        resp = await client.post(
            "/hooks/not-a-uuid",
            content=payload,
            headers={"x-hub-signature-256": "sha256=abc"},
        )
        assert resp.status_code == 400


class TestSuccessfulIngestion:
    async def test_valid_webhook_returns_202(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Ingest EP 1")
        payload = b'{"event": "order.created", "amount": 100}'
        sig = make_signature(KNOWN_SECRET, payload)

        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 202

    async def test_response_contains_event_id_and_status(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Ingest EP 2")
        payload = b'{"event": "user.signed_up"}'
        sig = make_signature(KNOWN_SECRET, payload)

        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        data = resp.json()
        assert data["status"] == "accepted"
        assert "event_id" in data

    async def test_outbox_record_created(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Kafka EP")
        payload = b'{"event": "payment.succeeded"}'
        sig = make_signature(KNOWN_SECRET, payload)

        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 202
        event_id = resp.json()["event_id"]

        async with async_session_factory() as db:
            event = await db.get(Event, event_id)
            assert event is not None
            assert str(event.endpoint_id) == ep["id"]

            result = await db.execute(
                select(OutboxRecord).where(OutboxRecord.event_id == event_id)
            )
            outbox = result.scalar_one_or_none()
            assert outbox is not None, "OutboxRecord should be created"
            assert outbox.status == "pending"
            assert outbox.publish_key == str(ep["id"])


class TestIdempotency:
    async def test_duplicate_idempotency_key_returns_duplicate(self, client, auth_headers, workspace_id):
        ep = await create_endpoint(client, auth_headers, workspace_id, "Idempotent EP")
        payload = b'{"event": "duplicate"}'
        sig = make_signature(KNOWN_SECRET, payload)
        idempotency_key = "unique-key-xyz-1234"

        headers = {
            "x-hub-signature-256": sig,
            "x-idempotency-key": idempotency_key,
        }

        first = await client.post(f"/hooks/{ep['id']}", content=payload, headers=headers)
        second = await client.post(f"/hooks/{ep['id']}", content=payload, headers=headers)

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json().get("status") == "duplicate"
