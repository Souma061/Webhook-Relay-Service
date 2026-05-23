import pytest
import hmac
import hashlib
from httpx import AsyncClient

async def test_receive_webhook_no_signature(client: AsyncClient):
    # Try to send webhook to a fake endpoint
    resp = await client.post(
        "/hooks/00000000-0000-0000-0000-000000000000",
        json={"data": "test"}
    )
    # The gateway currently doesn't check if the endpoint exists right away (it's async),
    # but without a signature it should either pass or fail depending on settings.
    # Wait, the current implementation checks signature IF hmac_secret exists.
    # Let's create an endpoint first.
    ep_resp = await client.post("/api/endpoints/", json={"name": "Gateway Test"})
    ep_id = ep_resp.json()["id"]
    
    # Missing signature header
    webhook_resp = await client.post(
        f"/hooks/{ep_id}",
        json={"data": "test"}
    )
    assert webhook_resp.status_code == 401
    assert "missing signature" in webhook_resp.json()["detail"].lower()

async def test_receive_webhook_invalid_signature(client: AsyncClient):
    ep_resp = await client.post("/api/endpoints/", json={"name": "Gateway Test 2"})
    ep_id = ep_resp.json()["id"]
    
    webhook_resp = await client.post(
        f"/hooks/{ep_id}",
        json={"data": "test"},
        headers={"x-hub-signature-256": "sha256=invalid"}
    )
    assert webhook_resp.status_code == 401
    assert "invalid signature" in webhook_resp.json()["detail"].lower()

async def test_receive_webhook_success(client: AsyncClient):
    ep_resp = await client.post("/api/endpoints/", json={"name": "Gateway Test 3"})
    ep_id = ep_resp.json()["id"]
    secret = ep_resp.json()["hmac_secret"].encode()
    
    payload = b'{"data": "test"}'
    signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    
    webhook_resp = await client.post(
        f"/hooks/{ep_id}",
        content=payload,
        headers={"x-hub-signature-256": f"sha256={signature}"}
    )
    assert webhook_resp.status_code == 202
    assert webhook_resp.json()["status"] == "accepted"
    assert "event_id" in webhook_resp.json()
