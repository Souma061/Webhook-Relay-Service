from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.core.database import async_session_factory
from app.middleware.rbac import get_current_user, get_workspace_membership, require_workspace_role
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership
from app.schemas.auth import (
    AddMemberRequest,
    UpdateMemberRoleRequest,
    WorkspaceCreate,
    WorkspaceMembershipOut,
    WorkspaceOut,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/api/workspaces", tags=["Workspaces"])

_SLUGIFY_RE = re.compile(r"[^a-z0-9-]")


def _parse_uuid(value: str, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid {label} format")


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    slug = _SLUGIFY_RE.sub("", slug)
    return slug[:63] or "workspace"


@router.get("/", response_model=list[WorkspaceOut])
async def list_workspaces(user: User = Depends(get_current_user)):
    async with async_session_factory() as db:
        result = await db.execute(
            select(Workspace, WorkspaceMembership.role)
            .join(WorkspaceMembership, Workspace.id == WorkspaceMembership.workspace_id)
            .where(WorkspaceMembership.user_id == user.id)
            .order_by(Workspace.created_at.desc())
        )
        rows = result.all()
        return [
            WorkspaceOut(
                id=ws.id,
                name=ws.name,
                slug=ws.slug,
                created_at=ws.created_at,
                role=role,
            )
            for ws, role in rows
        ]


@router.post("/", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, user: User = Depends(get_current_user)):
    async with async_session_factory() as db:
        ws = Workspace(name=body.name, slug=_slugify(f"{body.name}-{user.id}"))
        db.add(ws)
        await db.flush()

        membership = WorkspaceMembership(user_id=user.id, workspace_id=ws.id, role="owner")
        db.add(membership)
        await db.commit()
        await db.refresh(ws)

        return WorkspaceOut(
            id=ws.id, name=ws.name, slug=ws.slug, created_at=ws.created_at, role="owner"
        )


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    ws = membership.workspace
    return WorkspaceOut(
        id=ws.id, name=ws.name, slug=ws.slug, created_at=ws.created_at, role=membership.role,
    )


@router.put("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    body: WorkspaceUpdate,
    membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    ws = membership.workspace
    if body.name is not None:
        ws.name = body.name
    async with async_session_factory() as db:
        db.add(ws)
        await db.commit()
        await db.refresh(ws)
    return WorkspaceOut(
        id=ws.id, name=ws.name, slug=ws.slug, created_at=ws.created_at, role=membership.role,
    )


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    membership: WorkspaceMembership = Depends(require_workspace_role("owner")),
):
    async with async_session_factory() as db:
        await db.delete(membership.workspace)
        await db.commit()


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMembershipOut])
async def list_members(
    membership: WorkspaceMembership = Depends(require_workspace_role("viewer")),
):
    async with async_session_factory() as db:
        result = await db.execute(
            select(
                WorkspaceMembership.user_id,
                WorkspaceMembership.workspace_id,
                WorkspaceMembership.role,
                WorkspaceMembership.joined_at,
                User.email,
                User.display_name,
            )
            .join(User, WorkspaceMembership.user_id == User.id)
            .where(WorkspaceMembership.workspace_id == membership.workspace_id)
        )
        rows = result.all()
        return [
            WorkspaceMembershipOut(
                user_id=row.user_id,
                workspace_id=row.workspace_id,
                role=row.role,
                joined_at=row.joined_at,
                email=row.email,
                display_name=row.display_name,
            )
            for row in rows
        ]


@router.post("/{workspace_id}/members", response_model=WorkspaceMembershipOut, status_code=201)
async def add_member(
    body: AddMemberRequest,
    _membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    if body.role not in ("admin", "member", "viewer"):
        raise HTTPException(400, "role must be admin, member, or viewer")

    async with async_session_factory() as db:
        user = await db.execute(select(User).where(User.email == body.email))
        user = user.scalar_one_or_none()
        if not user:
            raise HTTPException(404, "user not found")

        existing = await db.get(
            WorkspaceMembership, (user.id, _membership.workspace_id)
        )
        if existing:
            raise HTTPException(409, "user is already a member")

        membership = WorkspaceMembership(
            user_id=user.id, workspace_id=_membership.workspace_id, role=body.role,
        )
        db.add(membership)
        await db.commit()
        await db.refresh(membership)

        return WorkspaceMembershipOut(
            user_id=membership.user_id,
            workspace_id=membership.workspace_id,
            role=membership.role,
            joined_at=membership.joined_at,
            email=user.email,
            display_name=user.display_name,
        )


@router.put("/{workspace_id}/members/{user_id}", response_model=WorkspaceMembershipOut)
async def update_member_role(
    user_id: str,
    body: UpdateMemberRoleRequest,
    _membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    if body.role not in ("admin", "member", "viewer"):
        raise HTTPException(400, "role must be admin, member, or viewer")

    target_uid = _parse_uuid(user_id, "user_id")

    async with async_session_factory() as db:
        membership = await db.get(
            WorkspaceMembership, (target_uid, _membership.workspace_id)
        )
        if not membership:
            raise HTTPException(404, "membership not found")
        if membership.role == "owner":
            raise HTTPException(403, "cannot change owner role")

        membership.role = body.role
        await db.commit()

        user = await db.get(User, target_uid)
        return WorkspaceMembershipOut(
            user_id=membership.user_id,
            workspace_id=membership.workspace_id,
            role=membership.role,
            joined_at=membership.joined_at,
            email=user.email if user else None,
            display_name=user.display_name if user else None,
        )


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(
    user_id: str,
    _membership: WorkspaceMembership = Depends(require_workspace_role("admin")),
):
    target_uid = _parse_uuid(user_id, "user_id")

    async with async_session_factory() as db:
        membership = await db.get(
            WorkspaceMembership, (target_uid, _membership.workspace_id)
        )
        if not membership:
            raise HTTPException(404, "membership not found")
        if membership.role == "owner":
            raise HTTPException(403, "cannot remove owner")
        await db.delete(membership)
        await db.commit()
