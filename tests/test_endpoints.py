import pytest
from httpx import AsyncClient

async def test_create_endpoint(client: AsyncClient):
    response = await client.post("/api/endpoints/", json={"name": "Test Endpoint"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Endpoint"
    assert "id" in data
    assert "hmac_secret" in data
    assert data["is_active"] is True

async def test_list_endpoints(client: AsyncClient):
    # Create one first
    await client.post("/api/endpoints/", json={"name": "List Endpoint"})
    
    response = await client.get("/api/endpoints/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(ep["name"] == "List Endpoint" for ep in data)

async def test_rotate_secret(client: AsyncClient):
    create_resp = await client.post("/api/endpoints/", json={"name": "Rotate Endpoint"})
    ep_id = create_resp.json()["id"]
    old_secret = create_resp.json()["hmac_secret"]
    
    rotate_resp = await client.post(f"/api/endpoints/{ep_id}/rotate")
    assert rotate_resp.status_code == 200
    new_secret = rotate_resp.json()["hmac_secret"]
    assert new_secret != old_secret
    assert len(new_secret) == 64  # hex string of 32 bytes

async def test_create_and_list_routes(client: AsyncClient):
    # Create endpoint
    ep_resp = await client.post("/api/endpoints/", json={"name": "Route Endpoint"})
    ep_id = ep_resp.json()["id"]
    
    # Create route
    route_data = {
        "name": "Test Route",
        "url": "http://example.com/webhook",
        "method": "POST"
    }
    route_resp = await client.post(f"/api/endpoints/{ep_id}/routes", json=route_data)
    assert route_resp.status_code == 201
    assert route_resp.json()["name"] == "Test Route"
    assert route_resp.json()["url"] == "http://example.com/webhook"
    
    # List routes
    list_resp = await client.get(f"/api/endpoints/{ep_id}/routes")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["name"] == "Test Route"

async def test_invalid_uuid(client: AsyncClient):
    resp = await client.get("/api/endpoints/not-a-uuid")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid endpoint_id format"
