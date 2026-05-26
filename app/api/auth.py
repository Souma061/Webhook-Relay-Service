from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.config import settings
from app.core.redis import get_redis
from app.core.security import create_access_token, hash_password, verify_password
from app.middleware.rbac import get_current_user
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

_SLUGIFY_RE = re.compile(r"[^a-z0-9-]")


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    slug = _SLUGIFY_RE.sub("", slug)
    return slug[:63] or "workspace"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def _check_auth_rate_limit(request: Request, email: str, action: str) -> None:
    try:
        redis = get_redis()
    except RuntimeError:
        return

    client_host = request.client.host if request.client else "unknown"
    safe_email = _normalize_email(email)
    keys = [
        f"auth-rl:{action}:ip:{client_host}",
        f"auth-rl:{action}:email:{safe_email}",
    ]

    for key in keys:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > settings.auth_rate_limit_per_minute:
            raise HTTPException(429, "too many authentication attempts")


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: UserCreate, request: Request):
    await _check_auth_rate_limit(request, body.email, "register")
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")

    email = _normalize_email(body.email)
    async with async_session_factory() as db:
        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none():
            raise HTTPException(409, "email already registered")

        user = User(
            email=email,
            display_name=body.display_name,
            password_hash=hash_password(body.password),
        )
        db.add(user)
        await db.flush()

        ws = Workspace(
            name=f"{body.display_name}'s Workspace",
            slug=_slugify(f"{body.display_name}-{user.id}"),
        )
        db.add(ws)
        await db.flush()

        membership = WorkspaceMembership(
            user_id=user.id, workspace_id=ws.id, role="owner"
        )
        db.add(membership)
        await db.commit()
        await db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    await _check_auth_rate_limit(request, body.email, "login")
    email = _normalize_email(body.email)
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid email or password")

    if not user.is_active:
        raise HTTPException(403, "account is disabled")

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
