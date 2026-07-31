"""Workspace members, invitations and API key lifecycle."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    api_keys,
    audit_log,
    tenant_invitations,
    tenant_memberships,
    tenant_subscriptions,
    saas_plans,
    users,
)
from services.product.entitlements import effective_entitlements, usage_permitted

from ..auth import get_current_user, require_role
from ..db import get_conn, get_tenant_scoped_conn
from ..workspace import ensure_workspace_user, tenant_uuid

router = APIRouter(prefix="/v1/account", tags=["account"])
_ADMIN_ROLES = ("OWNER", "ADMIN")
_MEMBER_ROLES = {"OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER", "VIEWER"}


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    display_name: str | None
    role: str
    mfa_enabled: bool
    joined_at: datetime


class InvitationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = "VIEWER"
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationResponse(BaseModel):
    id: str
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    invitation_token: str | None = None


class InvitationAcceptRequest(BaseModel):
    invitation_token: str = Field(min_length=20, max_length=300)


class ApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[Literal["read", "write", "admin"]] = Field(default_factory=lambda: ["read"], min_length=1)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    key: str | None = None


async def _audit(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: str,
    object_type: str,
    object_id: uuid.UUID,
) -> None:
    await conn.execute(
        audit_log.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            details={},
        )
    )


def _invitation(row, *, token: str | None = None) -> InvitationResponse:
    return InvitationResponse(
        id=str(row.id),
        email=row.email,
        role=row.role,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        invitation_token=token,
    )


def _api_key(row, *, key: str | None = None) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=str(row.id),
        name=row.name,
        key_prefix=row.key_prefix,
        scopes=row.scopes or [],
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        key=key,
    )


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[MemberResponse]:
    rows = (
        await conn.execute(
            sa.select(
                tenant_memberships.c.id,
                tenant_memberships.c.user_id,
                tenant_memberships.c.role,
                tenant_memberships.c.created_at,
                users.c.email,
                users.c.display_name,
                users.c.mfa_enabled,
            )
            .join(users, users.c.id == tenant_memberships.c.user_id)
            .where(tenant_memberships.c.tenant_id == tenant_uuid(user))
            .order_by(users.c.email)
        )
    ).all()
    return [
        MemberResponse(
            id=str(row.id),
            user_id=str(row.user_id),
            email=row.email,
            display_name=row.display_name,
            role=row.role,
            mfa_enabled=row.mfa_enabled,
            joined_at=row.created_at,
        )
        for row in rows
    ]


@router.patch("/members/{membership_id}", response_model=MemberResponse)
async def update_member_role(
    membership_id: uuid.UUID,
    body: dict[str, str],
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
) -> MemberResponse:
    role = str(body.get("role", "")).upper()
    if role not in _MEMBER_ROLES:
        raise HTTPException(status_code=422, detail="invalid member role")
    if role == "OWNER" and user.role != "OWNER":
        raise HTTPException(status_code=403, detail="only an owner can grant the owner role")

    tenant_id = tenant_uuid(user)
    current = (
        await conn.execute(
            sa.select(tenant_memberships)
            .where(
                tenant_memberships.c.id == membership_id,
                tenant_memberships.c.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).first()
    if current is None:
        raise HTTPException(status_code=404, detail="member not found")
    if current.role == "OWNER" and role != "OWNER":
        owner_ids = (
            await conn.execute(
                sa.select(tenant_memberships.c.id)
                .where(
                    tenant_memberships.c.tenant_id == tenant_id,
                    tenant_memberships.c.role == "OWNER",
                )
                .with_for_update()
            )
        ).scalars().all()
        if len(owner_ids) <= 1:
            raise HTTPException(status_code=409, detail="the workspace must keep at least one owner")

    actor_user_id = await ensure_workspace_user(conn, user)
    row = (
        await conn.execute(
            tenant_memberships.update()
            .where(
                tenant_memberships.c.id == membership_id,
                tenant_memberships.c.tenant_id == tenant_id,
            )
            .values(role=role)
            .returning(tenant_memberships)
        )
    ).first()
    member = (
        await conn.execute(
            sa.select(users).where(users.c.id == row.user_id)
        )
    ).one()
    await _audit(
        conn,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="membership.role_updated",
        object_type="tenant_membership",
        object_id=membership_id,
    )
    await conn.commit()
    return MemberResponse(
        id=str(row.id),
        user_id=str(row.user_id),
        email=member.email,
        display_name=member.display_name,
        role=row.role,
        mfa_enabled=member.mfa_enabled,
        joined_at=row.created_at,
    )


@router.get("/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
) -> list[InvitationResponse]:
    rows = (
        await conn.execute(
            sa.select(tenant_invitations)
            .where(tenant_invitations.c.tenant_id == tenant_uuid(user))
            .order_by(tenant_invitations.c.created_at.desc())
        )
    ).all()
    return [_invitation(row) for row in rows]


@router.post("/invitations", response_model=InvitationResponse, status_code=201)
async def create_invitation(
    body: InvitationRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
) -> InvitationResponse:
    role = body.role.upper()
    if role not in _MEMBER_ROLES:
        raise HTTPException(status_code=422, detail="invalid member role")
    if role == "OWNER" and user.role != "OWNER":
        raise HTTPException(status_code=403, detail="only an owner can invite another owner")
    entitlement_row = (
        await conn.execute(
            sa.select(
                tenant_subscriptions.c.entitlements_override,
                saas_plans.c.entitlements,
            )
            .join(saas_plans, saas_plans.c.code == tenant_subscriptions.c.plan_code)
            .where(tenant_subscriptions.c.tenant_id == tenant_uuid(user))
        )
    ).first()
    if entitlement_row is not None:
        entitlements = effective_entitlements(
            dict(entitlement_row.entitlements or {}),
            dict(entitlement_row.entitlements_override or {}),
        )
        seat_limit = entitlements.get("users")
        occupied = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(tenant_memberships)
                .where(tenant_memberships.c.tenant_id == tenant_uuid(user))
            )
        ).scalar_one()
        pending = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(tenant_invitations)
                .where(
                    tenant_invitations.c.tenant_id == tenant_uuid(user),
                    tenant_invitations.c.accepted_at.is_(None),
                    tenant_invitations.c.revoked_at.is_(None),
                    tenant_invitations.c.expires_at > datetime.now(timezone.utc),
                )
            )
        ).scalar_one()
        if not usage_permitted(seat_limit, int(occupied) + int(pending)):
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "SEAT_LIMIT",
                    "limit": seat_limit,
                    "usage": int(occupied) + int(pending),
                },
            )
    token = f"pi_{tenant_uuid(user)}_{secrets.token_urlsafe(32)}"
    user_id = await ensure_workspace_user(conn, user)
    invitation_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    await conn.execute(
        tenant_invitations.insert().values(
            id=invitation_id,
            tenant_id=tenant_uuid(user),
            email=str(body.email).casefold(),
            role=role,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            invited_by=user_id,
            expires_at=expires_at,
        )
    )
    await _audit(
        conn,
        tenant_id=tenant_uuid(user),
        actor_user_id=user_id,
        action="invitation.created",
        object_type="tenant_invitation",
        object_id=invitation_id,
    )
    row = (
        await conn.execute(sa.select(tenant_invitations).where(tenant_invitations.c.id == invitation_id))
    ).one()
    await conn.commit()
    return _invitation(row, token=token)


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
) -> None:
    user_id = await ensure_workspace_user(conn, user)
    result = await conn.execute(
        tenant_invitations.update()
        .where(
            tenant_invitations.c.id == invitation_id,
            tenant_invitations.c.tenant_id == tenant_uuid(user),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="invitation not found")
    await _audit(
        conn,
        tenant_id=tenant_uuid(user),
        actor_user_id=user_id,
        action="invitation.revoked",
        object_type="tenant_invitation",
        object_id=invitation_id,
    )
    await conn.commit()


@router.post("/invitations/accept", response_model=MemberResponse)
async def accept_invitation(
    body: InvitationAcceptRequest,
    conn: AsyncConnection = Depends(get_conn),
    user: AuthenticatedUser = Depends(get_current_user),
) -> MemberResponse:
    token_parts = body.invitation_token.split("_", 2)
    if len(token_parts) != 3 or token_parts[0] != "pi":
        raise HTTPException(status_code=404, detail="invitation is invalid or expired")
    try:
        invitation_tenant_id = uuid.UUID(token_parts[1])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="invitation is invalid or expired") from exc
    await conn.execute(
        sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(invitation_tenant_id)},
    )
    token_hash = hashlib.sha256(body.invitation_token.encode("utf-8")).hexdigest()
    invitation = (
        await conn.execute(
            sa.select(tenant_invitations).where(
                tenant_invitations.c.token_hash == token_hash,
                tenant_invitations.c.accepted_at.is_(None),
                tenant_invitations.c.revoked_at.is_(None),
                tenant_invitations.c.expires_at > datetime.now(timezone.utc),
            )
        )
    ).first()
    if invitation is None:
        raise HTTPException(status_code=404, detail="invitation is invalid or expired")
    if not user.email or user.email.casefold() != invitation.email.casefold():
        raise HTTPException(status_code=403, detail="invitation email does not match authenticated user")
    await conn.execute(
        pg_insert(users)
        .values(id=uuid.uuid4(), email=user.email, display_name=user.email.split("@", 1)[0])
        .on_conflict_do_nothing(index_elements=[users.c.email])
    )
    member = (await conn.execute(sa.select(users).where(users.c.email == user.email))).one()
    row = (
        await conn.execute(
            sa.select(tenant_memberships).where(
                tenant_memberships.c.tenant_id == invitation.tenant_id,
                tenant_memberships.c.user_id == member.id,
            )
        )
    ).first()
    if row is None:
        row = (
            await conn.execute(
                tenant_memberships.insert()
                .values(
                    id=uuid.uuid4(),
                    tenant_id=invitation.tenant_id,
                    user_id=member.id,
                    role=invitation.role,
                )
                .returning(tenant_memberships)
            )
        ).one()
    await conn.execute(
        tenant_invitations.update()
        .where(tenant_invitations.c.id == invitation.id)
        .values(accepted_at=datetime.now(timezone.utc))
    )
    await _audit(
        conn,
        tenant_id=invitation.tenant_id,
        actor_user_id=member.id,
        action="invitation.accepted",
        object_type="tenant_invitation",
        object_id=invitation.id,
    )
    await conn.commit()
    return MemberResponse(
        id=str(row.id),
        email=member.email,
        display_name=member.display_name,
        role=row.role,
        mfa_enabled=member.mfa_enabled,
        joined_at=row.created_at,
    )


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
) -> list[ApiKeyResponse]:
    rows = (
        await conn.execute(
            sa.select(api_keys)
            .where(api_keys.c.tenant_id == tenant_uuid(user))
            .order_by(api_keys.c.created_at.desc())
        )
    ).all()
    return [_api_key(row) for row in rows]


@router.post("/api-keys", response_model=ApiKeyResponse, status_code=201)
async def create_api_key(
    body: ApiKeyRequest,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
) -> ApiKeyResponse:
    token = f"pk_{tenant_uuid(user)}_{secrets.token_urlsafe(32)}"
    key_id = uuid.uuid4()
    user_id = await ensure_workspace_user(conn, user)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        if body.expires_in_days
        else None
    )
    await conn.execute(
        api_keys.insert().values(
            id=key_id,
            tenant_id=tenant_uuid(user),
            name=body.name,
            key_prefix=token[:12],
            key_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            scopes=list(dict.fromkeys(body.scopes)),
            created_by=user_id,
            expires_at=expires_at,
        )
    )
    await _audit(
        conn,
        tenant_id=tenant_uuid(user),
        actor_user_id=user_id,
        action="api_key.created",
        object_type="api_key",
        object_id=key_id,
    )
    row = (await conn.execute(sa.select(api_keys).where(api_keys.c.id == key_id))).one()
    await conn.commit()
    return _api_key(row, key=token)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: uuid.UUID,
    conn: AsyncConnection = Depends(get_tenant_scoped_conn),
    user: AuthenticatedUser = Depends(require_role(*_ADMIN_ROLES)),
) -> None:
    user_id = await ensure_workspace_user(conn, user)
    result = await conn.execute(
        api_keys.update()
        .where(api_keys.c.id == key_id, api_keys.c.tenant_id == tenant_uuid(user))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="API key not found")
    await _audit(
        conn,
        tenant_id=tenant_uuid(user),
        actor_user_id=user_id,
        action="api_key.revoked",
        object_type="api_key",
        object_id=key_id,
    )
    await conn.commit()
