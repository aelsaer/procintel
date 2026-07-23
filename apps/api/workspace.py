"""Shared tenant identity helpers for private workspace routers."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import tenant_memberships, users


def tenant_uuid(user: AuthenticatedUser) -> uuid.UUID:
    if not user.tenant_id:
        raise ValueError("authenticated user has no tenant")
    return uuid.UUID(user.tenant_id)


async def ensure_workspace_user(conn: AsyncConnection, user: AuthenticatedUser) -> uuid.UUID:
    email = user.email or f"{user.subject}@oidc.local"
    existing = (await conn.execute(sa.select(users.c.id).where(users.c.email == email))).first()
    if existing is not None:
        user_id = existing.id
    else:
        user_id = uuid.uuid4()
        await conn.execute(
            users.insert().values(
                id=user_id,
                email=email,
                display_name=email.split("@", 1)[0],
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
        .on_conflict_do_update(
            index_elements=[tenant_memberships.c.tenant_id, tenant_memberships.c.user_id],
            set_={"role": user.role},
        )
    )
    return user_id
