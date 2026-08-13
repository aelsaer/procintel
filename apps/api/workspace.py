"""Shared tenant identity helpers for private workspace routers."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import tenant_memberships, users


def tenant_uuid(user: AuthenticatedUser) -> uuid.UUID:
    if not user.tenant_id:
        raise ValueError("authenticated user has no tenant")
    return uuid.UUID(user.tenant_id)


async def ensure_workspace_user(conn: AsyncConnection, user: AuthenticatedUser) -> uuid.UUID:
    if user.auth_method == "API_KEY":
        if not user.user_id:
            raise HTTPException(status_code=403, detail="API key has no workspace actor")
        return uuid.UUID(user.user_id)

    if user.auth_method == "OIDC":
        if not user.user_id:
            raise HTTPException(status_code=403, detail="Organization provisioning is required")
        user_id = uuid.UUID(user.user_id)
        membership_exists = (
            await conn.execute(
                sa.select(tenant_memberships.c.id).where(
                    tenant_memberships.c.tenant_id == tenant_uuid(user),
                    tenant_memberships.c.user_id == user_id,
                )
            )
        ).first()
        if membership_exists is None:
            raise HTTPException(status_code=403, detail="Workspace membership is inactive")
        await conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(mfa_enabled=user.mfa_verified)
        )
        return user_id

    # Local development auth is the only path allowed to bootstrap identity
    # records without an OIDC subject binding.
    email = user.email or f"{user.subject}@oidc.local"
    existing = (await conn.execute(sa.select(users.c.id).where(users.c.email == email))).first()
    if existing is not None:
        user_id = existing.id
        await conn.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(mfa_enabled=user.mfa_verified)
        )
    else:
        user_id = uuid.uuid4()
        await conn.execute(
            users.insert().values(
                id=user_id,
                email=email,
                display_name=email.split("@", 1)[0],
                mfa_enabled=user.mfa_verified,
            )
        )

    await conn.execute(
        pg_insert(tenant_memberships)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid(user),
            user_id=user_id,
            role=user.role,
        )
        .on_conflict_do_nothing(
            index_elements=[tenant_memberships.c.tenant_id, tenant_memberships.c.user_id],
        )
    )
    return user_id
