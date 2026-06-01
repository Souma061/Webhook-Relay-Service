import hmac
import hashlib
from httpx import AsyncClient


ENDPOINT_PATH = "/api/workspaces/{workspace_id}/endpoints"
KNOWN_SECRET = "known-test-secret-00112233445566778899aabbccddeeff"


async def _create_endpoint(client, auth_headers, workspace_id, name="Gateway Test"):
    resp = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": name, "hmac_secret": KNOWN_SECRET},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


async def test_receive_webhook_no_signature(client: AsyncClient, auth_headers: dict, workspace_id: str):
    ep = await _create_endpoint(client, auth_headers, workspace_id, "No Sig EP")

    webhook_resp = await client.post(
        f"/hooks/{ep['id']}",
        json={"data": "test"},
    )
    assert webhook_resp.status_code == 401
    assert "missing signature" in webhook_resp.json()["detail"].lower()


async def test_receive_webhook_invalid_signature(client: AsyncClient, auth_headers: dict, workspace_id: str):
    ep = await _create_endpoint(client, auth_headers, workspace_id, "Bad Sig EP")

    webhook_resp = await client.post(
        f"/hooks/{ep['id']}",
        json={"data": "test"},
        headers={"x-hub-signature-256": "sha256=invalid"},
    )
    assert webhook_resp.status_code == 401
    assert "invalid signature" in webhook_resp.json()["detail"].lower()


async def test_receive_webhook_success(client: AsyncClient, auth_headers: dict, workspace_id: str):
    ep = await _create_endpoint(client, auth_headers, workspace_id, "Success EP")
    secret = KNOWN_SECRET.encode()

    payload = b'{"data": "test"}'
    signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()

    webhook_resp = await client.post(
        f"/hooks/{ep['id']}",
        content=payload,
        headers={"x-hub-signature-256": f"sha256={signature}"},
    )
    assert webhook_resp.status_code == 202
    assert webhook_resp.json()["status"] == "accepted"
    assert "event_id" in webhook_resp.json()
