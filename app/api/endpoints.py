import uuid
import secrets

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.endpoint import Endpoint
from app.models.route import Route
from app.schemas.api import (
    EndpointCreate,
    EndpointUpdate,
    EndpointOut,
    RouteCreate,
    RouteUpdate,
    RouteOut,
    SecretRotateOut,
)

router = APIRouter(prefix="/api/endpoints")


def _parse_uuid(value: str, label: str = "id") -> uuid.UUID:
    """Safely parse a UUID string, raising HTTP 400 on invalid format."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid {label} format")


# ── Endpoint CRUD ─────────────────────────────────────────────────────────────

@router.post("/", response_model=EndpointOut, status_code=201)
async def create_endpoint(body: EndpointCreate):
    async with async_session_factory() as db:
        ep = Endpoint(
            name=body.name,
            hmac_secret=body.hmac_secret or secrets.token_hex(32),
        )
        db.add(ep)
        await db.commit()
        await db.refresh(ep)
        return ep


@router.get("/", response_model=list[EndpointOut])
async def list_endpoints():
    async with async_session_factory() as db:
        result = await db.execute(select(Endpoint).order_by(Endpoint.created_at.desc()))
        return result.scalars().all()


@router.get("/{endpoint_id}", response_model=EndpointOut)
async def get_endpoint(endpoint_id: str):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep:
            raise HTTPException(404, "endpoint not found")
        return ep


@router.put("/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(endpoint_id: str, body: EndpointUpdate):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep:
            raise HTTPException(404, "endpoint not found")
        if body.name is not None:
            ep.name = body.name
        if body.is_active is not None:
            ep.is_active = body.is_active
        await db.commit()
        await db.refresh(ep)
        return ep


@router.post("/{endpoint_id}/rotate", response_model=SecretRotateOut)
async def rotate_secret(endpoint_id: str):
    """Generate a new HMAC secret. The old secret is invalidated immediately."""
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep:
            raise HTTPException(404, "endpoint not found")
        new_secret = secrets.token_hex(32)
        ep.hmac_secret = new_secret
        await db.commit()
        return SecretRotateOut(hmac_secret=new_secret)


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(endpoint_id: str):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep:
            raise HTTPException(404, "endpoint not found")
        await db.delete(ep)
        await db.commit()


# ── Route CRUD ────────────────────────────────────────────────────────────────

@router.post("/{endpoint_id}/routes", response_model=RouteOut, status_code=201)
async def add_route(endpoint_id: str, body: RouteCreate):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        ep = await db.get(Endpoint, ep_id)
        if not ep:
            raise HTTPException(404, "endpoint not found")
        route = Route(endpoint_id=ep.id, **body.model_dump())
        db.add(route)
        await db.commit()
        await db.refresh(route)
        return route


@router.get("/{endpoint_id}/routes", response_model=list[RouteOut])
async def list_routes(endpoint_id: str):
    ep_id = _parse_uuid(endpoint_id, "endpoint_id")
    async with async_session_factory() as db:
        result = await db.execute(
            select(Route).where(Route.endpoint_id == ep_id).order_by(Route.created_at)
        )
        return result.scalars().all()


@router.put("/routes/{route_id}", response_model=RouteOut)
async def update_route(route_id: str, body: RouteUpdate):
    r_id = _parse_uuid(route_id, "route_id")
    async with async_session_factory() as db:
        route = await db.get(Route, r_id)
        if not route:
            raise HTTPException(404, "route not found")
        update_data = body.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(route, field, value)
        await db.commit()
        await db.refresh(route)
        return route


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(route_id: str):
    r_id = _parse_uuid(route_id, "route_id")
    async with async_session_factory() as db:
        route = await db.get(Route, r_id)
        if not route:
            raise HTTPException(404, "route not found")
        await db.delete(route)
        await db.commit()
"""
endpoints.py — Admin CRUD API for endpoints and routes.

Provides REST endpoints to manage webhook endpoints and their routes:
  POST   /api/endpoints                      create endpoint
  GET    /api/endpoints                      list all endpoints
  GET    /api/endpoints/{id}                 get endpoint detail
  PUT    /api/endpoints/{id}                 update name / is_active
  POST   /api/endpoints/{id}/rotate          rotate HMAC secret (returns new secret once)
  DELETE /api/endpoints/{id}                 delete endpoint (cascades to routes)

  POST   /api/endpoints/{id}/routes          add route to endpoint
  GET    /api/endpoints/{id}/routes          list routes on endpoint
  PUT    /api/endpoints/routes/{id}          update route config
  DELETE /api/endpoints/routes/{id}          remove route

All UUID path params are validated with _parse_uuid() → HTTP 400 on bad format.
"""
