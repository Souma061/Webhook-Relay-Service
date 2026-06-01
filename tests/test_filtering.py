import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient

from app.core.database import async_session_factory
from app.models.route import Route
from app.models.event import Event
from workers.transform_worker import _process

ENDPOINT_PATH = "/api/workspaces/{workspace_id}/endpoints"
ROUTE_DETAIL_PATH = "/api/workspaces/{workspace_id}/routes"


async def test_route_filter_validation(client: AsyncClient, auth_headers: dict, workspace_id: str):
    ep_resp = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": "Filter Endpoint"},
        headers=auth_headers,
    )
    assert ep_resp.status_code == 201
    ep_id = ep_resp.json()["id"]

    routes_url = f"{ENDPOINT_PATH.format(workspace_id=workspace_id)}/{ep_id}/routes"
    route_data_invalid = {
        "name": "Invalid Route",
        "url": "https://example.com/webhook",
        "filter_expression": "event_type == 'payment.succeeded",
    }
    resp = await client.post(routes_url, json=route_data_invalid, headers=auth_headers)
    assert resp.status_code == 422
    assert "Invalid JMESPath expression" in resp.text

    route_data_valid = {
        "name": "Valid Route",
        "url": "https://example.com/webhook",
        "filter_expression": "event_type == 'payment.succeeded'",
    }
    resp = await client.post(routes_url, json=route_data_valid, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["filter_expression"] == "event_type == 'payment.succeeded'"
    route_id = resp.json()["id"]

    update_resp = await client.put(
        f"{ROUTE_DETAIL_PATH.format(workspace_id=workspace_id)}/{route_id}",
        json={"filter_expression": "amount > "},
        headers=auth_headers,
    )
    assert update_resp.status_code == 422
    assert "Invalid JMESPath expression" in update_resp.text

    update_resp = await client.put(
        f"{ROUTE_DETAIL_PATH.format(workspace_id=workspace_id)}/{route_id}",
        json={"filter_expression": "amount > `100`"},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["filter_expression"] == "amount > `100`"


async def test_transform_worker_filtering(client: AsyncClient, auth_headers: dict, workspace_id: str):
    ep_resp = await client.post(
        ENDPOINT_PATH.format(workspace_id=workspace_id),
        json={"name": "Worker Filter Endpoint"},
        headers=auth_headers,
    )
    assert ep_resp.status_code == 201
    ep_id = uuid.UUID(ep_resp.json()["id"])

    async with async_session_factory() as db:
        route_match = Route(
            endpoint_id=ep_id,
            name="Route Match",
            url="http://example.com/match",
            filter_expression="event == 'payment.succeeded'",
        )
        route_skip = Route(
            endpoint_id=ep_id,
            name="Route Skip",
            url="http://example.com/skip",
            filter_expression="event == 'payment.failed'",
        )
        route_all = Route(
            endpoint_id=ep_id,
            name="Route All",
            url="http://example.com/all",
            filter_expression=None,
        )
        db.add_all([route_match, route_skip, route_all])
        await db.commit()
        await db.refresh(route_match)
        await db.refresh(route_skip)
        await db.refresh(route_all)

        event = Event(
            endpoint_id=ep_id,
            request_body={"event": "payment.succeeded", "amount": 150},
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        event_id = str(event.id)

    producer = MagicMock()
    producer.send_and_wait = AsyncMock()

    await _process(producer, event_id, str(ep_id))

    assert producer.send_and_wait.call_count == 2

    called_route_ids = [call.kwargs["value"]["route_id"] for call in producer.send_and_wait.call_args_list]
    assert str(route_match.id) in called_route_ids
    assert str(route_all.id) in called_route_ids
    assert str(route_skip.id) not in called_route_ids
