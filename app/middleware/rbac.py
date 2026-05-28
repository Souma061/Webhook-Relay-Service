from __future__ import annotations

import uuid as uuid_pkg

from fastapi import Depends, HTTPException, status
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.core.security import decode_access_token, oauth2_scheme
from app.models.user import User
from app.models.workspace import WorkspaceMembership

ROLE_HIERARCHY = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}

_REVOKED_PREFIX = "revoked:jti:"


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token payload")

    jti = payload.get("jti")
    if jti:
        try:
            redis = get_redis()
            if await redis.exists(f"{_REVOKED_PREFIX}{jti}"):
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except RuntimeError:
            import logging
            logging.getLogger(__name__).warning(
                "Redis unavailable during jti revocation check — skipping"
            )

    async with async_session_factory() as db:
        user = await db.get(User, uuid_pkg.UUID(user_id))
        if not user or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found or inactive")
        return user


async def get_workspace_membership(
    workspace_id: str,
    user: User = Depends(get_current_user),
) -> WorkspaceMembership:
    try:
        ws_id = uuid_pkg.UUID(workspace_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid workspace id")

    async with async_session_factory() as db:
        membership = await db.get(
            WorkspaceMembership, (user.id, ws_id)
        )
        if not membership:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not a member of this workspace")
        return membership


def require_workspace_role(minimum_role: str):
    async def checker(
        workspace_id: str,
        membership: WorkspaceMembership = Depends(get_workspace_membership),
    ) -> WorkspaceMembership:
        user_level = ROLE_HIERARCHY.get(membership.role, -1)
        required_level = ROLE_HIERARCHY.get(minimum_role, 99)
        if user_level < required_level:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"requires role '{minimum_role}' or higher",
            )
        return membership
    return checker
