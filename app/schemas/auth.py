from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


# ── User ────────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: str
    password: str
    display_name: str


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Auth ────────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Workspace ───────────────────────────────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str
    created_at: datetime
    role: str | None = None

    model_config = {"from_attributes": True}


class WorkspaceUpdate(BaseModel):
    name: str | None = None


class WorkspaceMembershipOut(BaseModel):
    user_id: UUID
    workspace_id: UUID
    role: str
    joined_at: datetime
    email: str | None = None
    display_name: str | None = None

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    email: str
    role: str = "member"


class UpdateMemberRoleRequest(BaseModel):
    role: str
