"""Tenant session context for RLS-aware background workers."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import tenants


async def all_tenant_ids(conn: AsyncConnection) -> list[uuid.UUID]:
    """List workspace IDs from the shared tenant registry (which is not RLS-scoped)."""
    return list((await conn.execute(sa.select(tenants.c.id).order_by(tenants.c.id))).scalars().all())


@asynccontextmanager
async def tenant_session(
    conn: AsyncConnection,
    tenant_id: uuid.UUID,
    *,
    role: str = "WORKER",
) -> AsyncIterator[None]:
    """Set session-level RLS context across worker functions that commit internally."""
    previous_tenant = (
        await conn.execute(sa.text("SELECT current_setting('app.tenant_id', true)"))
    ).scalar_one_or_none()
    previous_role = (
        await conn.execute(sa.text("SELECT current_setting('app.role', true)"))
    ).scalar_one_or_none()
    await conn.execute(
        sa.text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
        {"tenant_id": str(tenant_id)},
    )
    await conn.execute(
        sa.text("SELECT set_config('app.role', :role, false)"),
        {"role": role},
    )
    try:
        yield
    except Exception:
        if conn.in_transaction():
            await conn.rollback()
        raise
    finally:
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant_id, false)"),
            {"tenant_id": previous_tenant or ""},
        )
        await conn.execute(
            sa.text("SELECT set_config('app.role', :role, false)"),
            {"role": previous_role or ""},
        )
