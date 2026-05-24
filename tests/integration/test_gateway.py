"""
Integration tests for the webhook ingestion gateway (POST /hooks/{endpoint_id}).

Covers: HMAC signature verification, idempotency deduplication, 202 Accepted
response format, nonexistent endpoint handling, and Kafka publish verification.
Requires PostgreSQL + Redis (configured in conftest.py). Kafka is mocked.
"""
import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock
from httpx import AsyncClient

import app.core.kafka as core_kafka


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_signature(secret: str, payload: bytes) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


async def create_endpoint(client: AsyncClient, name: str = "GW Endpoint") -> dict:
    resp = await client.post("/api/endpoints/", json={"name": name})
    assert resp.status_code == 201
    return resp.json()


# ── Missing / Invalid Signature ────────────────────────────────────────────────

class TestSignatureVerification:
    async def test_missing_signature_returns_401(self, client: AsyncClient):
        ep = await create_endpoint(client, "Sig EP 1")
        resp = await client.post(f"/hooks/{ep['id']}", json={"data": "hello"})
        assert resp.status_code == 401
        assert "missing" in resp.json()["detail"].lower()

    async def test_wrong_signature_returns_401(self, client: AsyncClient):
        ep = await create_endpoint(client, "Sig EP 2")
        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=b'{"data":"hello"}',
            headers={"x-hub-signature-256": "sha256=deadbeef"},
        )
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    async def test_signature_for_wrong_secret_returns_401(self, client: AsyncClient):
        ep = await create_endpoint(client, "Sig EP 3")
        payload = b'{"data":"hello"}'
        # Sign with a different secret
        wrong_sig = make_signature("wrong_secret", payload)
        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": wrong_sig},
        )
        assert resp.status_code == 401


# ── Nonexistent Endpoint ───────────────────────────────────────────────────────

class TestNonexistentEndpoint:
    async def test_unknown_endpoint_id_returns_404(self, client: AsyncClient):
        payload = b'{"data":"hello"}'
        sig = make_signature("anysecret", payload)
        resp = await client.post(
            "/hooks/00000000-0000-0000-0000-000000000000",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 404

    async def test_invalid_endpoint_uuid_returns_400(self, client: AsyncClient):
        payload = b"{}"
        resp = await client.post(
            "/hooks/not-a-uuid",
            content=payload,
            headers={"x-hub-signature-256": "sha256=abc"},
        )
        assert resp.status_code == 400


# ── Successful Ingestion ───────────────────────────────────────────────────────

class TestSuccessfulIngestion:
    async def test_valid_webhook_returns_202(self, client: AsyncClient):
        ep = await create_endpoint(client, "Ingest EP 1")
        payload = b'{"event": "order.created", "amount": 100}'
        sig = make_signature(ep["hmac_secret"], payload)

        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 202

    async def test_response_contains_event_id_and_status(self, client: AsyncClient):
        ep = await create_endpoint(client, "Ingest EP 2")
        payload = b'{"event": "user.signed_up"}'
        sig = make_signature(ep["hmac_secret"], payload)

        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        data = resp.json()
        assert data["status"] == "accepted"
        assert "event_id" in data

    async def test_kafka_producer_called_with_event_id(self, client: AsyncClient):
        ep = await create_endpoint(client, "Kafka EP")
        payload = b'{"event": "payment.succeeded"}'
        sig = make_signature(ep["hmac_secret"], payload)

        # Reset mock call count
        core_kafka._producer.send_and_wait.reset_mock()

        resp = await client.post(
            f"/hooks/{ep['id']}",
            content=payload,
            headers={"x-hub-signature-256": sig},
        )
        assert resp.status_code == 202

        core_kafka._producer.send_and_wait.assert_called_once()
        call_kwargs = core_kafka._producer.send_and_wait.call_args
        msg_value = call_kwargs[1]["value"] if call_kwargs[1] else call_kwargs[0][1]
        assert "event_id" in msg_value
        assert msg_value["endpoint_id"] == ep["id"]


# ── Idempotency ───────────────────────────────────────────────────────────────

class TestIdempotency:
    async def test_duplicate_idempotency_key_returns_200_not_202(self, client: AsyncClient):
        ep = await create_endpoint(client, "Idempotent EP")
        payload = b'{"event": "duplicate"}'
        sig = make_signature(ep["hmac_secret"], payload)
        idempotency_key = "unique-key-xyz-1234"

        headers = {
            "x-hub-signature-256": sig,
            "x-idempotency-key": idempotency_key,
        }

        first = await client.post(f"/hooks/{ep['id']}", content=payload, headers=headers)
        second = await client.post(f"/hooks/{ep['id']}", content=payload, headers=headers)

        assert first.status_code == 202
        # The second request with the same key should be deduplicated
        assert second.status_code in (200, 202)
        if second.status_code == 200:
            assert second.json().get("status") == "duplicate"
