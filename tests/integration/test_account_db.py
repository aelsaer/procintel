"""`apps/api/routers/account.py` — workspace members, invitations, API keys.

Zero integration coverage existed for this router before this pass (only
`tests/unit/test_account_models.py`, which just instantiates the Pydantic
request models — no DB, no HTTP, no router). Each test uses its own fresh
tenant (`PROCINTEL_DEV_TENANT_ID` overrides the dev-auth bypass's default
`DEV_TENANT_ID`) so tests never collide on shared membership/invitation/
seat-limit counts, unlike sharing the fixed `DEV_TENANT_ID` would. Each
test also uses its own fresh dev-auth email (`users.email` is globally
unique, not tenant-scoped, so re-using the real dev-auth default
"owner@procintel.local" across tests collides with itself).

Skipped automatically unless $DATABASE_URL is set.
"""

import asyncio
import hashlib
import os
import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.main import app
from packages.domain.tables import (
    api_keys,
    audit_log,
    saas_plans,
    tenant_invitations,
    tenant_memberships,
    tenant_subscriptions,
    tenants,
    users,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")


def _asyncpg_url() -> str:
    if DATABASE_URL.startswith("postgresql://"):
        return "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://") :]
    return DATABASE_URL


def _fresh_env(monkeypatch, *, email: str | None = None) -> tuple[uuid.UUID, str]:
    tenant_id = uuid.uuid4()
    owner_email = email or f"owner-{uuid.uuid4().hex[:10]}@procintel.local"
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("PROCINTEL_DEV_AUTH", "1")
    monkeypatch.setenv("PROCINTEL_DEV_TENANT_ID", str(tenant_id))
    monkeypatch.setenv("PROCINTEL_DEV_EMAIL", owner_email)
    return tenant_id, owner_email


async def _seed_tenant(conn, tenant_id: uuid.UUID) -> None:
    """`get_tenant_scoped_conn` only auto-creates the `tenants` row as a
    side effect of an authenticated HTTP request; tests that seed rows
    directly (before making any HTTP call) need it created explicitly, or
    every FK referencing `tenants.id` fails."""
    await conn.execute(
        pg_insert(tenants).values(id=tenant_id, name="Test tenant", plan="PROFESSIONAL").on_conflict_do_nothing()
    )


async def _cleanup_tenant(engine, tenant_id: uuid.UUID, emails: list[str]) -> None:
    """`tenant_memberships` rows may exist either because a test seeded one
    directly, or because an endpoint (e.g. accept-invitation) created one —
    always delete memberships before users, or the FK blocks the delete."""
    async with engine.begin() as conn:
        await conn.execute(audit_log.delete().where(audit_log.c.tenant_id == tenant_id))
        await conn.execute(tenant_memberships.delete().where(tenant_memberships.c.tenant_id == tenant_id))
        await conn.execute(tenant_invitations.delete().where(tenant_invitations.c.tenant_id == tenant_id))
        await conn.execute(api_keys.delete().where(api_keys.c.tenant_id == tenant_id))
        await conn.execute(users.delete().where(users.c.email.in_(emails)))


async def test_member_role_update_blocks_demoting_the_last_owner(monkeypatch):
    tenant_id, owner_email = _fresh_env(monkeypatch)
    engine = create_async_engine(_asyncpg_url())
    user_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await _seed_tenant(conn, tenant_id)
            await conn.execute(users.insert().values(id=user_id, email=owner_email, display_name="owner"))
            await conn.execute(tenant_memberships.insert().values(
                id=membership_id, tenant_id=tenant_id, user_id=user_id, role="OWNER",
            ))

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(
                client.patch(f"/v1/account/members/{membership_id}", json={"role": "ANALYST"}),
                timeout=15,
            )
        assert response.status_code == 409
        assert "owner" in response.json()["detail"].lower()

        async with engine.connect() as conn:
            row = (await conn.execute(select(tenant_memberships.c.role).where(tenant_memberships.c.id == membership_id))).one()
            assert row.role == "OWNER"  # unchanged
    finally:
        await _cleanup_tenant(engine, tenant_id, [owner_email])
        await engine.dispose()


async def test_invitation_lifecycle_create_list_revoke(monkeypatch):
    tenant_id, owner_email = _fresh_env(monkeypatch)
    engine = create_async_engine(_asyncpg_url())
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/v1/account/invitations", json={"email": "new-analyst@example.test", "role": "analyst"})
            assert created.status_code == 201
            body = created.json()
            assert body["role"] == "ANALYST"
            assert body["email"] == "new-analyst@example.test"
            assert body["invitation_token"]  # only present on create
            invitation_id = body["id"]

            listed = await client.get("/v1/account/invitations")
            assert listed.status_code == 200
            assert any(row["id"] == invitation_id for row in listed.json())
            assert all(row["invitation_token"] is None for row in listed.json())  # never re-exposed

            revoked = await client.delete(f"/v1/account/invitations/{invitation_id}")
            assert revoked.status_code == 204

            listed_again = await client.get("/v1/account/invitations")
            revoked_row = next(row for row in listed_again.json() if row["id"] == invitation_id)
            assert revoked_row["revoked_at"] is not None
    finally:
        await _cleanup_tenant(engine, tenant_id, [owner_email])
        await engine.dispose()


async def test_invitation_accept_requires_matching_email_and_creates_membership(monkeypatch):
    tenant_id, owner_email = _fresh_env(monkeypatch)
    invited_email = f"invited-{uuid.uuid4().hex[:10]}@example.test"
    engine = create_async_engine(_asyncpg_url())
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/v1/account/invitations", json={"email": invited_email, "role": "viewer"})
            token = created.json()["invitation_token"]

            # wrong identity (still the seeded owner) — email mismatch
            mismatched = await client.post("/v1/account/invitations/accept", json={"invitation_token": token})
            assert mismatched.status_code == 403

        # switch the dev-auth identity to the invited email and accept for real
        monkeypatch.setenv("PROCINTEL_DEV_EMAIL", invited_email)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            accepted = await client.post("/v1/account/invitations/accept", json={"invitation_token": token})
            assert accepted.status_code == 200
            accepted_body = accepted.json()
            assert accepted_body["email"] == invited_email
            assert accepted_body["role"] == "VIEWER"

            # re-accepting the same (now-accepted) token is rejected, not silently repeated
            replay = await client.post("/v1/account/invitations/accept", json={"invitation_token": token})
            assert replay.status_code == 404

        async with engine.connect() as conn:
            membership = (
                await conn.execute(
                    select(tenant_memberships.c.role).where(
                        tenant_memberships.c.tenant_id == tenant_id,
                        tenant_memberships.c.user_id == uuid.UUID(accepted_body["user_id"]),
                    )
                )
            ).one()
            assert membership.role == "VIEWER"
    finally:
        await _cleanup_tenant(engine, tenant_id, [owner_email, invited_email])
        await engine.dispose()


async def test_seat_limit_blocks_invitation_when_plan_limit_reached(monkeypatch):
    tenant_id, owner_email = _fresh_env(monkeypatch)
    engine = create_async_engine(_asyncpg_url())
    plan_code = f"TEST_SEAT_LIMIT_{uuid.uuid4().hex[:8]}"
    owner_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    subscription_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await _seed_tenant(conn, tenant_id)
            await conn.execute(users.insert().values(id=owner_id, email=owner_email, display_name="owner"))
            await conn.execute(tenant_memberships.insert().values(
                id=membership_id, tenant_id=tenant_id, user_id=owner_id, role="OWNER",
            ))
            await conn.execute(saas_plans.insert().values(
                code=plan_code, name="Seat Limit Test Plan", description="test", entitlements={"users": 1}, is_public=False,
            ))
            await conn.execute(tenant_subscriptions.insert().values(
                id=subscription_id, tenant_id=tenant_id, plan_code=plan_code, status="ACTIVE",
            ))

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/account/invitations", json={"email": "second-seat@example.test", "role": "viewer"})
        assert response.status_code == 402
        detail = response.json()["detail"]
        assert detail["code"] == "SEAT_LIMIT"
        assert detail["limit"] == 1
        assert detail["usage"] == 1  # the seeded OWNER membership already occupies the one seat
    finally:
        async with engine.begin() as conn:
            await conn.execute(tenant_subscriptions.delete().where(tenant_subscriptions.c.id == subscription_id))
            await conn.execute(saas_plans.delete().where(saas_plans.c.code == plan_code))
        await _cleanup_tenant(engine, tenant_id, [owner_email])
        await engine.dispose()


async def test_api_key_lifecycle_reveals_plaintext_only_once(monkeypatch):
    tenant_id, owner_email = _fresh_env(monkeypatch)
    engine = create_async_engine(_asyncpg_url())
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/v1/account/api-keys", json={"name": "CI key", "scopes": ["read", "write"]})
            assert created.status_code == 201
            body = created.json()
            plaintext_key = body["key"]
            assert plaintext_key.startswith("pk_")
            assert body["key_prefix"] == plaintext_key[:12]
            key_id = body["id"]

            listed = await client.get("/v1/account/api-keys")
            assert listed.status_code == 200
            assert all(row["key"] is None for row in listed.json())  # plaintext never re-exposed

            revoked = await client.delete(f"/v1/account/api-keys/{key_id}")
            assert revoked.status_code == 204

        async with engine.connect() as conn:
            row = (await conn.execute(select(api_keys.c.key_hash, api_keys.c.revoked_at).where(api_keys.c.id == uuid.UUID(key_id)))).one()
            assert row.key_hash == hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()
            assert row.revoked_at is not None
    finally:
        await _cleanup_tenant(engine, tenant_id, [owner_email])
        await engine.dispose()
