import pytest
from httpx import AsyncClient

ENDPOINT_PATH = "/api/workspaces/{workspace_id}/endpoints"
ROUTES_PATH = "/api/workspaces/{workspace_id}/endpoints/{ep_id}/routes"
ROUTE_DETAIL_PATH = "/api/workspaces/{workspace_id}/routes"


class TestCreateEndpoint:
    async def test_create_returns_201_with_fields(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        resp = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Acme Webhooks"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Acme Webhooks"
        assert "id" in data
        assert data["is_active"] is True

    async def test_create_with_custom_secret(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        resp = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Custom Secret EP", "hmac_secret": "mysecret123"},
            headers=auth_headers,
        )
        assert resp.status_code == 201

    async def test_create_requires_name(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        resp = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestListEndpoints:
    async def test_list_returns_200_and_array(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "List EP 1"},
            headers=auth_headers,
        )
        resp = await client.get(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_newly_created_endpoint_appears_in_list(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Unique EP for list"},
            headers=auth_headers,
        )
        resp = await client.get(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            headers=auth_headers,
        )
        names = [ep["name"] for ep in resp.json()]
        assert "Unique EP for list" in names


class TestGetEndpoint:
    async def test_get_existing_endpoint(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        create = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Get EP"},
            headers=auth_headers,
        )
        ep_id = create.json()["id"]
        resp = await client.get(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == ep_id

    async def test_get_nonexistent_returns_404(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        resp = await client.get(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/00000000-0000-0000-0000-000000000099",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_get_invalid_uuid_returns_400(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        resp = await client.get(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/not-a-uuid",
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "invalid endpoint_id format" in resp.json()["detail"]


class TestUpdateEndpoint:
    async def test_update_name(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        create = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Old Name"},
            headers=auth_headers,
        )
        ep_id = create.json()["id"]
        resp = await client.put(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}",
            json={"name": "New Name"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    async def test_update_is_active_to_false(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        create = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Active EP"},
            headers=auth_headers,
        )
        ep_id = create.json()["id"]
        resp = await client.put(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}",
            json={"is_active": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_update_nonexistent_returns_404(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        resp = await client.put(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/00000000-0000-0000-0000-000000000099",
            json={"name": "Ghost"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestSecretRotation:
    async def test_rotate_generates_new_64_char_secret(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        create = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Rotate EP"},
            headers=auth_headers,
        )
        ep_id = create.json()["id"]

        resp = await client.post(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}/rotate",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        new_secret = resp.json()["hmac_secret"]
        assert len(new_secret) == 64

    async def test_rotate_nonexistent_returns_404(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        resp = await client.post(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/00000000-0000-0000-0000-000000000099/rotate",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestDeleteEndpoint:
    async def test_delete_returns_204(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        create = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Delete EP"},
            headers=auth_headers,
        )
        ep_id = create.json()["id"]
        resp = await client.delete(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    async def test_deleted_endpoint_returns_404_on_get(self, client: AsyncClient, auth_headers: dict, workspace_id: str):
        create = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": "Gone EP"},
            headers=auth_headers,
        )
        ep_id = create.json()["id"]
        await client.delete(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}",
            headers=auth_headers,
        )
        resp = await client.get(
            f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestRouteCRUD:
    async def _create_endpoint(self, client, auth_headers, workspace_id, name="Route Owner"):
        resp = await client.post(
            ENDPOINT_PATH.format(workspace_id=workspace_id),
            json={"name": name},
            headers=auth_headers,
        )
        return resp.json()["id"]

    async def test_create_route_returns_201(self, client, auth_headers, workspace_id):
        ep_id = await self._create_endpoint(client, auth_headers, workspace_id, "Route Create EP")
        resp = await client.post(
            ROUTES_PATH.format(workspace_id=workspace_id, ep_id=ep_id),
            json={"name": "My Route", "url": "https://dest.example.com/hook"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Route"
        assert data["url"] == "https://dest.example.com/hook"
        assert data["is_active"] is True
        assert data["method"] == "POST"

    async def test_list_routes_for_endpoint(self, client, auth_headers, workspace_id):
        ep_id = await self._create_endpoint(client, auth_headers, workspace_id, "Route List EP")
        routes_url = ROUTES_PATH.format(workspace_id=workspace_id, ep_id=ep_id)
        await client.post(routes_url, json={"name": "R1", "url": "https://a.com"}, headers=auth_headers)
        await client.post(routes_url, json={"name": "R2", "url": "https://b.com"}, headers=auth_headers)

        resp = await client.get(routes_url, headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        names = {r["name"] for r in resp.json()}
        assert names == {"R1", "R2"}

    async def test_create_route_on_nonexistent_endpoint_returns_404(self, client, auth_headers, workspace_id):
        resp = await client.post(
            ROUTES_PATH.format(workspace_id=workspace_id, ep_id="00000000-0000-0000-0000-000000000099"),
            json={"name": "X", "url": "https://x.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_update_route_url(self, client, auth_headers, workspace_id):
        ep_id = await self._create_endpoint(client, auth_headers, workspace_id, "Route Update EP")
        create = await client.post(
            ROUTES_PATH.format(workspace_id=workspace_id, ep_id=ep_id),
            json={"name": "Update Route", "url": "https://old.com"},
            headers=auth_headers,
        )
        route_id = create.json()["id"]

        resp = await client.put(
            f"{ROUTE_DETAIL_PATH.format(workspace_id=workspace_id)}/{route_id}",
            json={"url": "https://new.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://new.com"
        assert resp.json()["name"] == "Update Route"

    async def test_update_route_toggle_active(self, client, auth_headers, workspace_id):
        ep_id = await self._create_endpoint(client, auth_headers, workspace_id, "Toggle Route EP")
        create = await client.post(
            ROUTES_PATH.format(workspace_id=workspace_id, ep_id=ep_id),
            json={"name": "Toggle Route", "url": "https://t.com"},
            headers=auth_headers,
        )
        route_id = create.json()["id"]

        resp = await client.put(
            f"{ROUTE_DETAIL_PATH.format(workspace_id=workspace_id)}/{route_id}",
            json={"is_active": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_delete_route_returns_204(self, client, auth_headers, workspace_id):
        ep_id = await self._create_endpoint(client, auth_headers, workspace_id, "Route Delete EP")
        create = await client.post(
            ROUTES_PATH.format(workspace_id=workspace_id, ep_id=ep_id),
            json={"name": "Delete Route", "url": "https://del.com"},
            headers=auth_headers,
        )
        route_id = create.json()["id"]

        resp = await client.delete(
            f"{ROUTE_DETAIL_PATH.format(workspace_id=workspace_id)}/{route_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    async def test_delete_nonexistent_route_returns_404(self, client, auth_headers, workspace_id):
        resp = await client.delete(
            f"{ROUTE_DETAIL_PATH.format(workspace_id=workspace_id)}/00000000-0000-0000-0000-000000000099",
            headers=auth_headers,
        )
        assert resp.status_code == 404
