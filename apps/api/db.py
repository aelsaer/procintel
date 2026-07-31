"""Async DB engine + FastAPI dependency.

A single module-level engine, created lazily on first use (not at import
time) so importing `apps.api.main` — e.g. to generate the OpenAPI schema in
a test — never requires `DATABASE_URL` to be set.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import uuid

import sqlalchemy as sa
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from packages.auth.jwt_verifier import AuthenticatedUser

from .auth import dev_auth_enabled, get_current_user

_engines: dict[tuple[int, str], AsyncEngine] = {}


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def get_engine() -> AsyncEngine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    loop_key = id(asyncio.get_running_loop())
    key = (loop_key, database_url)
    if key not in _engines:
        _engines[key] = create_async_engine(
            _to_asyncpg_url(database_url),
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engines[key]


async def get_conn() -> AsyncIterator[AsyncConnection]:
    """Unauthenticated connection — used by the public-data routers
    (contracts/processes/search/buyers/companies). Procurement data is
    explicitly "shared public data" per description.txt §38, not
    tenant-scoped, so no auth/RLS context applies here."""
    engine = get_engine()
    async with engine.connect() as conn:
        yield conn


async def get_tenant_scoped_conn(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AsyncIterator[AsyncConnection]:
    """Authenticated connection for tenant-scoped resources (alert rules
    and friends, §38). Wraps the request in one transaction and sets the
    session-local `app.tenant_id`/`app.role` Postgres settings the RLS
    policies in `db/migrations/16_row_level_security.sql` key off —
    `set_config(..., is_local=true)` is used (not string-interpolated
    `SET LOCAL`) so the tenant id is a genuine bound parameter, never
    concatenated into SQL text.

    RLS itself only takes effect if this connection authenticates as the
    non-owner `procintel_app` role (see that migration's own comment) —
    set DATABASE_URL accordingly for this to actually enforce anything."""
    if not user.tenant_id:
        raise HTTPException(status_code=400, detail="token carries no tenant_id claim")
    try:
        uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tenant_id claim is not a valid UUID") from exc

    engine = get_engine()
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": user.tenant_id},
            )
            await conn.execute(sa.text("SELECT set_config('app.role', :role, true)"), {"role": user.role})
            if dev_auth_enabled():
                await conn.execute(
                    sa.text(
                        """
                        INSERT INTO tenants (id, name, plan)
                        VALUES (CAST(:tenant_id AS uuid), 'Procintel Local Workspace', 'PROFESSIONAL')
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {"tenant_id": user.tenant_id},
                )
                await conn.execute(
                    sa.text(
                        """
                        INSERT INTO tenant_subscriptions (
                            id, tenant_id, plan_code, status, billing_provider,
                            current_period_start, current_period_end
                        )
                        SELECT
                            gen_random_uuid(), CAST(:tenant_id AS uuid),
                            'PROFESSIONAL', 'ACTIVE', 'MANUAL', now(),
                            now() + interval '100 years'
                        WHERE EXISTS (
                            SELECT 1 FROM saas_plans WHERE code = 'PROFESSIONAL'
                        )
                        ON CONFLICT (tenant_id) DO NOTHING
                        """
                    ),
                    {"tenant_id": user.tenant_id},
                )
            yield conn
