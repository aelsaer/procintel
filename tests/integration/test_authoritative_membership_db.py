"""OIDC subject bindings make database memberships authoritative for RBAC."""

import os
import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine

import apps.api.auth as auth_module
from apps.api.main import app
from packages.auth.jwt_verifier import AuthenticatedUser
from packages.domain.tables import (
    oidc_subject_tenant_bindings,
    tenant_memberships,
    tenants,
    users,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _asyncpg_url() -> str:
    if DATABASE_URL.startswith("postgresql://"):
        return "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://") :]
    return DATABASE_URL


class _Verifier:
    def __init__(self, user: AuthenticatedUser) -> None:
        self.user = user

    async def verify(self, token: str) -> AuthenticatedUser:
        return self.user


async def test_database_membership_overrides_token_role_and_revocation_is_immediate(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    subject = f"subject-{uuid.uuid4()}"
    email = f"member-{uuid.uuid4().hex[:10]}@example.test"
    issuer = f"https://issuer-{uuid.uuid4()}.example.test"
    engine = create_async_engine(_asyncpg_url())
    token_user = AuthenticatedUser(
        subject=subject,
        email=email,
        tenant_id=str(uuid.uuid4()),
        role="OWNER",
        auth_method="OIDC",
        mfa_verified=True,
        email_verified=True,
    )
    monkeypatch.setenv("OIDC_ISSUER_URL", issuer)
    monkeypatch.delenv("PROCINTEL_DEV_AUTH", raising=False)
    monkeypatch.setattr(auth_module, "get_verifier", lambda: _Verifier(token_user))

    try:
        async with engine.begin() as conn:
            await conn.execute(tenants.insert().values(id=tenant_id, name="RBAC test", plan="STARTER"))
            await conn.execute(users.insert().values(id=user_id, email=email, display_name="member"))
            await conn.execute(
                tenant_memberships.insert().values(
                    id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, role="VIEWER"
                )
            )
            await conn.execute(
                oidc_subject_tenant_bindings.insert().values(
                    id=uuid.uuid4(),
                    issuer=issuer,
                    subject=subject,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            )

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/v1/workspace/me", headers={"Authorization": "Bearer valid-token"}
            )
            assert response.status_code == 200
            assert response.json()["role"] == "VIEWER"

            async with engine.begin() as conn:
                await conn.execute(
                    tenant_memberships.delete().where(
                        tenant_memberships.c.tenant_id == tenant_id,
                        tenant_memberships.c.user_id == user_id,
                    )
                )

            revoked = await client.get(
                "/v1/workspace/me", headers={"Authorization": "Bearer valid-token"}
            )
            assert revoked.status_code == 403
            assert "inactive" in revoked.json()["detail"]
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                oidc_subject_tenant_bindings.delete().where(
                    oidc_subject_tenant_bindings.c.issuer == issuer,
                    oidc_subject_tenant_bindings.c.subject == subject,
                )
            )
            await conn.execute(
                tenant_memberships.delete().where(tenant_memberships.c.tenant_id == tenant_id)
            )
            await conn.execute(users.delete().where(users.c.id == user_id))
            await conn.execute(tenants.delete().where(tenants.c.id == tenant_id))
        await engine.dispose()
