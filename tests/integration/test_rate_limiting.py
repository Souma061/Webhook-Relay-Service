import pytest
import hmac
import hashlib
import json


pytestmark = pytest.mark.asyncio


def _sign(body: dict, secret: str) -> tuple[str, bytes]:
    raw = json.dumps(body).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return f"sha256={sig}", raw


class TestEndpointIngressRateLimit:
    async def _send_webhook(self, session_client, ep_id: str, body: dict, secret: str):
        sig, raw = _sign(body, secret)
        return await session_client.post(
            f"/hooks/{ep_id}",
            content=raw,
            headers={
                "x-hub-signature-256": sig,
                "content-type": "application/json",
            },
        )

    async def test_no_rate_limit_by_default(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "No RL", "hmac_secret": "s1"},
            headers=auth_headers,
        )
        ep = resp.json()
        assert ep.get("rate_limit_rps") is None

        body = {"event": "test"}
        for _ in range(5):
            resp = await self._send_webhook(session_client, ep["id"], body, "s1")
            assert resp.status_code == 202, f"expected 202, got {resp.status_code}: {resp.text[:200]}"

    async def test_returns_429_when_rate_limit_exceeded(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "Strict RL", "hmac_secret": "s2", "rate_limit_rps": 1},
            headers=auth_headers,
        )
        ep = resp.json()
        assert ep["rate_limit_rps"] == 1

        body = {"event": "burst"}
        accepted = 0
        rejected = 0
        for _ in range(70):
            resp = await self._send_webhook(session_client, ep["id"], body, "s2")
            if resp.status_code == 202:
                accepted += 1
            elif resp.status_code == 429:
                rejected += 1

        assert accepted > 0, "some requests should be accepted"
        assert rejected > 0, "rate limiter should reject excess requests"

    async def test_endpoint_create_response_includes_rate_limit_rps(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "Check Field", "hmac_secret": "s3", "rate_limit_rps": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["rate_limit_rps"] == 5

    async def test_update_rate_limit_rps(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "Updatable RL", "hmac_secret": "s4"},
            headers=auth_headers,
        )
        ep = resp.json()
        assert ep.get("rate_limit_rps") is None

        update_resp = await session_client.put(
            f"/api/workspaces/{workspace_id}/endpoints/{ep['id']}",
            json={"rate_limit_rps": 1},
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["rate_limit_rps"] == 1

        body = {"event": "after-update"}
        accepted = 0
        rejected = 0
        for _ in range(80):
            resp = await self._send_webhook(session_client, ep["id"], body, "s4")
            if resp.status_code == 202:
                accepted += 1
            elif resp.status_code == 429:
                rejected += 1

        assert accepted > 0
        assert rejected > 0

    async def test_zero_rate_limit_rps_blocks_all(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "Zero RL", "hmac_secret": "s5", "rate_limit_rps": 0},
            headers=auth_headers,
        )
        ep = resp.json()
        assert ep["rate_limit_rps"] == 0

        body = {"event": "blocked"}
        resp = await self._send_webhook(session_client, ep["id"], body, "s5")
        assert resp.status_code == 429


class TestRouteRateLimitSchema:
    async def test_route_create_accepts_rate_limit_rpm(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "Route RL Endpoint", "hmac_secret": "s6"},
            headers=auth_headers,
        )
        ep = resp.json()

        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints/{ep['id']}/routes",
            json={"name": "RL Route", "url": "https://example.com/hook", "rate_limit_rpm": 30},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["rate_limit_rpm"] == 30

    async def test_route_rate_limit_rpm_default_null(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "No Route RL", "hmac_secret": "s7"},
            headers=auth_headers,
        )
        ep = resp.json()

        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints/{ep['id']}/routes",
            json={"name": "No RL Route", "url": "https://example.com/hook"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json().get("rate_limit_rpm") is None

    async def test_route_update_rate_limit_rpm(self, auth_headers, session_client, workspace_id):
        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints",
            json={"name": "Update Route RL", "hmac_secret": "s8"},
            headers=auth_headers,
        )
        ep = resp.json()

        resp = await session_client.post(
            f"/api/workspaces/{workspace_id}/endpoints/{ep['id']}/routes",
            json={"name": "Update Target", "url": "https://example.com/hook"},
            headers=auth_headers,
        )
        route = resp.json()

        update_resp = await session_client.put(
            f"/api/workspaces/{workspace_id}/routes/{route['id']}",
            json={"rate_limit_rpm": 15},
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["rate_limit_rpm"] == 15
