"""Proves Postgres RLS actually blocks cross-tenant reads — not just that
the policy SQL exists. Skipped automatically unless $DATABASE_URL is set.

Seeds two tenants (each with one `alert_rules` row) via the superuser
connection (`DATABASE_URL` — the migration-owning role RLS doesn't apply
to), then opens a *second* connection as the restricted `procintel_app`
role (`db/migrations/16_row_level_security.sql`) and confirms:
`SET LOCAL app.tenant_id = <tenant A>` sees only tenant A's row, never
tenant B's, even though both physically exist in the same table.

`$PROCINTEL_APP_DATABASE_URL` lets a real deployment point this at a
rotated `procintel_app` password explicitly; if unset, it's derived from
`DATABASE_URL` by swapping in the migration's own documented dev-only
placeholder credentials (`procintel_app` / `CHANGE_ME_procintel_app`) —
correct for a freshly-migrated dev database, not for anything with a
rotated password (there, set the env var explicitly or this suite's
RLS-specific tests skip on connection failure).
"""

import os
import re
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import alert_rules, tenants, users

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")


def _asyncpg_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _app_role_url() -> str:
    override = os.environ.get("PROCINTEL_APP_DATABASE_URL")
    if override:
        return _asyncpg_url(override)
    # swap user:password in DATABASE_URL for the migration's dev-default app role
    return re.sub(r"//[^@]+@", "//procintel_app:CHANGE_ME_procintel_app@", _asyncpg_url(DATABASE_URL))


async def _seed_tenant_with_rule(conn) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, user_id, rule_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute(tenants.insert().values(id=tenant_id, name=f"Tenant {tenant_id}"))
    await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))
    await conn.execute(
        alert_rules.insert().values(
            id=rule_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name="RLS test rule",
            event_types=["contract.created"],
            filters={},
            schedule="IMMEDIATE",
            delivery_channels=[],
        )
    )
    await conn.commit()
    return tenant_id, rule_id


async def test_procintel_app_role_only_sees_its_own_tenants_alert_rules():
    superuser_engine = create_async_engine(_asyncpg_url(DATABASE_URL))
    try:
        async with superuser_engine.connect() as conn:
            tenant_a, rule_a = await _seed_tenant_with_rule(conn)
            tenant_b, rule_b = await _seed_tenant_with_rule(conn)

        app_engine = create_async_engine(_app_role_url())
        try:
            try:
                app_conn_cm = app_engine.connect()
                app_conn = await app_conn_cm.__aenter__()
            except Exception as exc:  # noqa: BLE001 — connecting as procintel_app can fail many ways
                pytest.skip(f"procintel_app role not reachable ({exc}) — set PROCINTEL_APP_DATABASE_URL explicitly")
                return

            try:
                async with app_conn.begin():
                    await app_conn.execute(
                        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_a)}
                    )
                    visible_ids = {row.id for row in (await app_conn.execute(select(alert_rules.c.id))).all()}
                assert rule_a in visible_ids
                assert rule_b not in visible_ids
            finally:
                await app_conn_cm.__aexit__(None, None, None)
        finally:
            await app_engine.dispose()
            async with superuser_engine.connect() as conn:
                await conn.execute(alert_rules.delete().where(alert_rules.c.id.in_([rule_a, rule_b])))
                await conn.execute(tenants.delete().where(tenants.c.id.in_([tenant_a, tenant_b])))
                await conn.commit()
    finally:
        await superuser_engine.dispose()


async def test_superuser_connection_bypasses_rls_by_design():
    """The migration's own documented caveat: the table-owning/superuser
    role (what DATABASE_URL normally points at) always sees every tenant's
    rows regardless of `app.tenant_id` — RLS only restricts `procintel_app`.
    This isn't a bug to fix, it's why the app must connect as
    `procintel_app` for RLS to mean anything."""
    engine = create_async_engine(_asyncpg_url(DATABASE_URL))
    try:
        async with engine.connect() as conn:
            tenant_a, rule_a = await _seed_tenant_with_rule(conn)
            tenant_b, rule_b = await _seed_tenant_with_rule(conn)
            try:
                async with conn.begin():
                    await conn.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_a)})
                    visible_ids = {row.id for row in (await conn.execute(select(alert_rules.c.id))).all()}
                assert rule_a in visible_ids
                assert rule_b in visible_ids  # bypassed — this is the superuser connection
            finally:
                await conn.execute(alert_rules.delete().where(alert_rules.c.id.in_([rule_a, rule_b])))
                await conn.execute(tenants.delete().where(tenants.c.id.in_([tenant_a, tenant_b])))
                await conn.commit()
    finally:
        await engine.dispose()
