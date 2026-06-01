import pytest
from httpx import AsyncClient


ENDPOINT_PATH = "/api/workspaces/{workspace_id}/endpoints"
ROUTE_PATH = "/api/workspaces/{workspace_id}/routes"


async def test_create_endpoint(client: AsyncClient, auth_headers: dict, workspace_id: str):
    response = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": "Test Endpoint"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Endpoint"
    assert "id" in data
    assert data["is_active"] is True


async def test_list_endpoints(client: AsyncClient, auth_headers: dict, workspace_id: str):
    await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": "List Endpoint"},
        headers=auth_headers,
    )

    response = await client.get(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(ep["name"] == "List Endpoint" for ep in data)


async def test_rotate_secret(client: AsyncClient, auth_headers: dict, workspace_id: str):
    create_resp = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": "Rotate Endpoint"},
        headers=auth_headers,
    )
    ep_id = create_resp.json()["id"]

    rotate_resp = await client.post(
        f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}/rotate",
        headers=auth_headers,
    )
    assert rotate_resp.status_code == 200
    new_secret = rotate_resp.json()["hmac_secret"]
    assert len(new_secret) == 64


async def test_create_and_list_routes(client: AsyncClient, auth_headers: dict, workspace_id: str):
    ep_resp = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": "Route Endpoint"},
        headers=auth_headers,
    )
    ep_id = ep_resp.json()["id"]

    route_data = {
        "name": "Test Route",
        "url": "https://example.com/webhook",
        "method": "POST",
    }
    routes_url = f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}/routes"
    route_resp = await client.post(routes_url, json=route_data, headers=auth_headers)
    assert route_resp.status_code == 201
    assert route_resp.json()["name"] == "Test Route"
    assert route_resp.json()["url"] == "https://example.com/webhook"

    list_resp = await client.get(routes_url, headers=auth_headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["name"] == "Test Route"


async def test_invalid_uuid(client: AsyncClient, auth_headers: dict, workspace_id: str):
    resp = await client.get(
        f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/not-a-uuid",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid endpoint_id format"
