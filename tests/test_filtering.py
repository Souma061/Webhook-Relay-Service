import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient

from app.core.database import async_session_factory
from app.models.route import Route
from app.models.event import Event
from workers.transform_worker import _process

async def test_route_filter_validation(client: AsyncClient):
    # 1. Create Endpoint
    ep_resp = await client.post("/api/endpoints/", json={"name": "Filter Endpoint"})
    assert ep_resp.status_code == 201
    ep_id = ep_resp.json()["id"]

    # 2. Try creating route with invalid JMESPath expression (unclosed quote)
    route_data_invalid = {
        "name": "Invalid Route",
        "url": "http://example.com/webhook",
        "filter_expression": "event_type == 'payment.succeeded"
    }
    resp = await client.post(f"/api/endpoints/{ep_id}/routes", json=route_data_invalid)
    assert resp.status_code == 422  # Pydantic field_validator raises ValueError -> 422 Unprocessable Entity
    assert "Invalid JMESPath expression" in resp.text

    # 3. Create route with valid JMESPath expression
    route_data_valid = {
        "name": "Valid Route",
        "url": "http://example.com/webhook",
        "filter_expression": "event_type == 'payment.succeeded'"
    }
    resp = await client.post(f"/api/endpoints/{ep_id}/routes", json=route_data_valid)
    assert resp.status_code == 201
    assert resp.json()["filter_expression"] == "event_type == 'payment.succeeded'"
    route_id = resp.json()["id"]

    # 4. Try updating route with invalid expression
    update_resp = await client.put(f"/api/endpoints/routes/{route_id}", json={
        "filter_expression": "amount > "
    })
    assert update_resp.status_code == 422
    assert "Invalid JMESPath expression" in update_resp.text

    # 5. Update route with valid expression
    update_resp = await client.put(f"/api/endpoints/routes/{route_id}", json={
        "filter_expression": "amount > `100`"
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["filter_expression"] == "amount > `100`"

async def test_transform_worker_filtering(client: AsyncClient):
    # 1. Create Endpoint
    ep_resp = await client.post("/api/endpoints/", json={"name": "Worker Filter Endpoint"})
    ep_id = uuid.UUID(ep_resp.json()["id"])

    # 2. Create three routes: one with matching filter, one with non-matching filter, one with no filter
    async with async_session_factory() as db:
        route_match = Route(
            endpoint_id=ep_id,
            name="Route Match",
            url="http://example.com/match",
            filter_expression="event == 'payment.succeeded'"
        )
        route_skip = Route(
            endpoint_id=ep_id,
            name="Route Skip",
            url="http://example.com/skip",
            filter_expression="event == 'payment.failed'"
        )
        route_all = Route(
            endpoint_id=ep_id,
            name="Route All",
            url="http://example.com/all",
            filter_expression=None
        )
        db.add_all([route_match, route_skip, route_all])
        await db.commit()
        await db.refresh(route_match)
        await db.refresh(route_skip)
        await db.refresh(route_all)

        # 3. Create Event in DB
        event = Event(
            endpoint_id=ep_id,
            request_body={"event": "payment.succeeded", "amount": 150}
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        
        event_id = str(event.id)

    # 4. Mock the producer
    producer = MagicMock()
    producer.send_and_wait = AsyncMock()

    # 5. Call transform worker _process method
    await _process(producer, event_id, str(ep_id))

    # 6. Verify which routes received messages
    # It should call send_and_wait for route_match and route_all, but NOT route_skip
    assert producer.send_and_wait.call_count == 2

    called_route_ids = [call.kwargs["value"]["route_id"] for call in producer.send_and_wait.call_args_list]
    assert str(route_match.id) in called_route_ids
    assert str(route_all.id) in called_route_ids
    assert str(route_skip.id) not in called_route_ids
