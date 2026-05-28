from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.url_security import redact_headers, validate_delivery_url
from app.middleware.rbac import get_workspace_membership, require_workspace_role
from app.models.endpoint import Endpoint
from app.models.route import Route
from app.models.workspace import WorkspaceMembership
from app.schemas.api import (
    EndpointCreate,
    EndpointOut,
    EndpointUpdate,
    RouteCreate,
    RouteOut,
    RouteUpdate,
    SecretRotateOut,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}")


def _parse_uuid(value: str, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid {label} format")


def _route_out(route: Route) -> RouteOut:
    return RouteOut.model_validate(route).model_copy(
        update={"headers": redact_headers(route.headers)}
    )


@router.get("/endpoints", response_model=list[EndpointOut])
async def list_endpoints(
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    async with async_session_factory() as db:
        result = await db.execute(
            select(Endpoint)
            .where(Endpoint.workspace_id == membership.workspace_id)
            .order_by(Endpoint.created_at.desc())
        )
        return result.scalars().all()


@router.post("/endpoints", response_model=EndpointOut, status_code=201)
async def create_endpoint(
    body: EndpointCreate,
    membership: WorkspaceMembership = Depends(require_workspace_role("member")),
):
    async with async_session_factory() as db:
        ep = Endpoint(
            workspace_id=membership.workspace_id,
            name=body.name,
            hmac_secret=body.hmac_secret or secrets.token_hex(32),
        )
        db.add(ep)
        await db.commit()
        await db.refresh(ep)
        return ep


@router.get("/endpoints/{endpoint_id}", response_model=EndpointOut)
async def get_endpoint(
    endpoint_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "endpoint not found")
        return ep


@router.put("/endpoints/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(
    endpoint_id: str,
    body: EndpointUpdate,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "endpoint not found")
        if body.name is not None:
            ep.name = body.name
        if body.is_active is not None:
            ep.is_active = body.is_active
        await db.commit()
        await db.refresh(ep)
        return ep


@router.delete("/endpoints/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "endpoint not found")
        await db.delete(ep)
        await db.commit()


@router.post("/endpoints/{endpoint_id}/rotate", response_model=SecretRotateOut)
async def rotate_secret(
    endpoint_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "endpoint not found")
        new_secret = secrets.token_hex(32)
        ep.hmac_secret = new_secret
        await db.commit()
        return SecretRotateOut(hmac_secret=new_secret)


@router.get("/endpoints/{endpoint_id}/routes", response_model=list[RouteOut])
async def list_routes(
    endpoint_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "endpoint not found")
        result = await db.execute(
            select(Route).where(Route.endpoint_id == ep_id).order_by(Route.created_at)
        )
        return [_route_out(route) for route in result.scalars().all()]


@router.post("/endpoints/{endpoint_id}/routes", response_model=RouteOut, status_code=201)
async def add_route(
    endpoint_id: str,
    body: RouteCreate,
    membership: WorkspaceMembership = Depends(require_workspace_role("member")),
):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    validate_delivery_url(body.url)
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "endpoint not found")
        route = Route(endpoint_id=ep.id, **body.model_dump())
        db.add(route)
        await db.commit()
        await db.refresh(route)
        return _route_out(route)


@router.put("/routes/{route_id}", response_model=RouteOut)
async def update_route(
    route_id: str,
    body: RouteUpdate,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    r_id = _parse_uuid(route_id, "route_id")
    async with async_session_factory() as db:
        route = await db.get(Route, r_id)
        if not route:
            raise HTTPException(404, "route not found")
        ep = await db.get(Endpoint, route.endpoint_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "route not found")
        update_data = body.model_dump(exclude_unset=True)
        if "url" in update_data and update_data["url"] is not None:
            validate_delivery_url(update_data["url"])
        for field, value in update_data.items():
            setattr(route, field, value)
        await db.commit()
        await db.refresh(route)
        return _route_out(route)


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(
    route_id: str,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    r_id = _parse_uuid(route_id, "route_id")
    async with async_session_factory() as db:
        route = await db.get(Route, r_id)
        if not route:
            raise HTTPException(404, "route not found")
        ep = await db.get(Endpoint, route.endpoint_id)
        if not ep or ep.workspace_id != membership.workspace_id:
            raise HTTPException(404, "route not found")
        await db.delete(route)
        await db.commit()
