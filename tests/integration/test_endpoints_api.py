"""
Integration tests for the Endpoints and Routes CRUD API.

Covers: create, list, get, update, delete endpoints;
create, list, update, delete routes; secret rotation; UUID validation.
Requires a running PostgreSQL instance (configured in conftest.py).
"""
import pytest
from httpx import AsyncClient


# ── Endpoint CRUD ──────────────────────────────────────────────────────────────

class TestCreateEndpoint:
    async def test_create_returns_201_with_fields(self, client: AsyncClient):
        resp = await client.post("/api/endpoints/", json={"name": "Acme Webhooks"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Acme Webhooks"
        assert "id" in data
        assert len(data["hmac_secret"]) == 64  # secrets.token_hex(32)
        assert data["is_active"] is True

    async def test_create_with_custom_secret(self, client: AsyncClient):
        resp = await client.post(
            "/api/endpoints/",
            json={"name": "Custom Secret EP", "hmac_secret": "mysecret123"},
        )
        assert resp.status_code == 201
        assert resp.json()["hmac_secret"] == "mysecret123"

    async def test_create_requires_name(self, client: AsyncClient):
        resp = await client.post("/api/endpoints/", json={})
        assert resp.status_code == 422  # Pydantic validation


class TestListEndpoints:
    async def test_list_returns_200_and_array(self, client: AsyncClient):
        await client.post("/api/endpoints/", json={"name": "List EP 1"})
        resp = await client.get("/api/endpoints/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_newly_created_endpoint_appears_in_list(self, client: AsyncClient):
        await client.post("/api/endpoints/", json={"name": "Unique EP for list"})
        resp = await client.get("/api/endpoints/")
        names = [ep["name"] for ep in resp.json()]
        assert "Unique EP for list" in names


class TestGetEndpoint:
    async def test_get_existing_endpoint(self, client: AsyncClient):
        create = await client.post("/api/endpoints/", json={"name": "Get EP"})
        ep_id = create.json()["id"]
        resp = await client.get(f"/api/endpoints/{ep_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ep_id

    async def test_get_nonexistent_returns_404(self, client: AsyncClient):
        resp = await client.get("/api/endpoints/00000000-0000-0000-0000-000000000099")
        assert resp.status_code == 404

    async def test_get_invalid_uuid_returns_400(self, client: AsyncClient):
        resp = await client.get("/api/endpoints/not-a-uuid")
        assert resp.status_code == 400
        assert "invalid endpoint_id format" in resp.json()["detail"]


class TestUpdateEndpoint:
    async def test_update_name(self, client: AsyncClient):
        create = await client.post("/api/endpoints/", json={"name": "Old Name"})
        ep_id = create.json()["id"]
        resp = await client.put(f"/api/endpoints/{ep_id}", json={"name": "New Name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    async def test_update_is_active_to_false(self, client: AsyncClient):
        create = await client.post("/api/endpoints/", json={"name": "Active EP"})
        ep_id = create.json()["id"]
        resp = await client.put(f"/api/endpoints/{ep_id}", json={"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_update_nonexistent_returns_404(self, client: AsyncClient):
        resp = await client.put(
            "/api/endpoints/00000000-0000-0000-0000-000000000099",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404


class TestSecretRotation:
    async def test_rotate_generates_new_64_char_secret(self, client: AsyncClient):
        create = await client.post("/api/endpoints/", json={"name": "Rotate EP"})
        ep_id = create.json()["id"]
        old_secret = create.json()["hmac_secret"]

        resp = await client.post(f"/api/endpoints/{ep_id}/rotate")
        assert resp.status_code == 200
        new_secret = resp.json()["hmac_secret"]
        assert new_secret != old_secret
        assert len(new_secret) == 64

    async def test_rotate_nonexistent_returns_404(self, client: AsyncClient):
        resp = await client.post("/api/endpoints/00000000-0000-0000-0000-000000000099/rotate")
        assert resp.status_code == 404


class TestDeleteEndpoint:
    async def test_delete_returns_204(self, client: AsyncClient):
        create = await client.post("/api/endpoints/", json={"name": "Delete EP"})
        ep_id = create.json()["id"]
        resp = await client.delete(f"/api/endpoints/{ep_id}")
        assert resp.status_code == 204

    async def test_deleted_endpoint_returns_404_on_get(self, client: AsyncClient):
        create = await client.post("/api/endpoints/", json={"name": "Gone EP"})
        ep_id = create.json()["id"]
        await client.delete(f"/api/endpoints/{ep_id}")
        resp = await client.get(f"/api/endpoints/{ep_id}")
        assert resp.status_code == 404


# ── Route CRUD ────────────────────────────────────────────────────────────────

class TestRouteCRUD:
    async def _create_endpoint(self, client: AsyncClient, name: str = "Route Owner") -> str:
        resp = await client.post("/api/endpoints/", json={"name": name})
        return resp.json()["id"]

    async def test_create_route_returns_201(self, client: AsyncClient):
        ep_id = await self._create_endpoint(client, "Route Create EP")
        resp = await client.post(
            f"/api/endpoints/{ep_id}/routes",
            json={"name": "My Route", "url": "https://dest.example.com/hook"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Route"
        assert data["url"] == "https://dest.example.com/hook"
        assert data["is_active"] is True
        assert data["method"] == "POST"

    async def test_list_routes_for_endpoint(self, client: AsyncClient):
        ep_id = await self._create_endpoint(client, "Route List EP")
        await client.post(f"/api/endpoints/{ep_id}/routes", json={"name": "R1", "url": "http://a.com"})
        await client.post(f"/api/endpoints/{ep_id}/routes", json={"name": "R2", "url": "http://b.com"})

        resp = await client.get(f"/api/endpoints/{ep_id}/routes")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        names = {r["name"] for r in resp.json()}
        assert names == {"R1", "R2"}

    async def test_create_route_on_nonexistent_endpoint_returns_404(self, client: AsyncClient):
        resp = await client.post(
            "/api/endpoints/00000000-0000-0000-0000-000000000099/routes",
            json={"name": "X", "url": "http://x.com"},
        )
        assert resp.status_code == 404

    async def test_update_route_url(self, client: AsyncClient):
        ep_id = await self._create_endpoint(client, "Route Update EP")
        create = await client.post(
            f"/api/endpoints/{ep_id}/routes",
            json={"name": "Update Route", "url": "http://old.com"},
        )
        route_id = create.json()["id"]

        resp = await client.put(
            f"/api/endpoints/routes/{route_id}",
            json={"url": "http://new.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["url"] == "http://new.com"
        assert resp.json()["name"] == "Update Route"  # unchanged

    async def test_update_route_toggle_active(self, client: AsyncClient):
        ep_id = await self._create_endpoint(client, "Toggle Route EP")
        create = await client.post(
            f"/api/endpoints/{ep_id}/routes",
            json={"name": "Toggle Route", "url": "http://t.com"},
        )
        route_id = create.json()["id"]

        resp = await client.put(
            f"/api/endpoints/routes/{route_id}", json={"is_active": False}
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_delete_route_returns_204(self, client: AsyncClient):
        ep_id = await self._create_endpoint(client, "Route Delete EP")
        create = await client.post(
            f"/api/endpoints/{ep_id}/routes",
            json={"name": "Delete Route", "url": "http://del.com"},
        )
        route_id = create.json()["id"]

        resp = await client.delete(f"/api/endpoints/routes/{route_id}")
        assert resp.status_code == 204

    async def test_delete_nonexistent_route_returns_404(self, client: AsyncClient):
        resp = await client.delete("/api/endpoints/routes/00000000-0000-0000-0000-000000000099")
        assert resp.status_code == 404
