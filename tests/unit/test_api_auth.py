"""`apps/api/auth.py`'s dependencies, called directly as plain async
functions (not through the full ASGI stack — `get_verifier()` is
monkeypatched to a fake, so no OIDC/JWKS setup is needed here). The full
request-level 401/403 behavior is covered by
tests/integration/test_rls_enforcement_db.py, which needs a real DB for
`get_tenant_scoped_conn`'s downstream query to succeed anyway.
"""

import pytest
from fastapi import HTTPException

import apps.api.auth as auth_module
from packages.auth.jwt_verifier import AuthenticatedUser, JwtVerificationError


class _FakeVerifier:
    def __init__(self, user: AuthenticatedUser | None = None, error: Exception | None = None) -> None:
        self._user = user
        self._error = error

    async def verify(self, token: str) -> AuthenticatedUser:
        if self._error:
            raise self._error
        return self._user


async def test_get_current_user_rejects_missing_authorization_header():
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.get_current_user(authorization=None)
    assert exc_info.value.status_code == 401


async def test_get_current_user_rejects_non_bearer_scheme():
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.get_current_user(authorization="Basic dXNlcjpwYXNz")
    assert exc_info.value.status_code == 401


async def test_get_current_user_rejects_a_token_the_verifier_flags(monkeypatch):
    monkeypatch.setattr(
        auth_module, "get_verifier", lambda: _FakeVerifier(error=JwtVerificationError("bad signature"))
    )
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.get_current_user(authorization="Bearer some.jwt.token")
    assert exc_info.value.status_code == 401


async def test_get_current_user_returns_the_verified_user(monkeypatch):
    expected = AuthenticatedUser(subject="u1", email="a@b.test", tenant_id="t1", role="ANALYST")
    monkeypatch.setattr(auth_module, "get_verifier", lambda: _FakeVerifier(user=expected))
    user = await auth_module.get_current_user(authorization="Bearer some.jwt.token")
    assert user == expected


async def test_get_current_user_enforces_mfa_after_database_role_resolution(monkeypatch):
    verified = AuthenticatedUser(
        subject="u1", email="a@b.test", tenant_id=None, role="VIEWER", mfa_verified=False
    )
    elevated = AuthenticatedUser(
        subject="u1", email="a@b.test", tenant_id="t1", role="OWNER", mfa_verified=False
    )
    monkeypatch.setattr(auth_module, "get_verifier", lambda: _FakeVerifier(user=verified))

    async def apply_binding(user):
        return elevated

    monkeypatch.setattr(auth_module, "_apply_subject_tenant_binding", apply_binding)
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.get_current_user(authorization="Bearer some.jwt.token")
    assert exc_info.value.status_code == 401
    assert "MFA" in str(exc_info.value.detail)


async def test_require_role_allows_a_permitted_role():
    check = auth_module.require_role("ANALYST", "ADMIN")
    user = AuthenticatedUser(subject="u1", email=None, tenant_id="t1", role="ANALYST")
    result = await check(user=user)
    assert result is user


async def test_require_role_rejects_a_role_not_in_the_allow_list():
    check = auth_module.require_role("ADMIN", "OWNER")
    user = AuthenticatedUser(subject="u1", email=None, tenant_id="t1", role="VIEWER")
    with pytest.raises(HTTPException) as exc_info:
        await check(user=user)
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_api_key_read_scope_allows_only_read_methods(method):
    assert auth_module.api_key_scope_permits(frozenset({"read"}), method)
    assert not auth_module.api_key_scope_permits(frozenset({"read"}), "POST")


def test_api_key_scope_hierarchy_enforces_write_and_admin():
    assert auth_module.api_key_scope_permits(frozenset({"write"}), "GET")
    assert auth_module.api_key_scope_permits(frozenset({"write"}), "PATCH")
    assert auth_module.api_key_scope_permits(frozenset({"admin"}), "DELETE")
    assert auth_module.api_key_scope_permits(frozenset({"*"}), "POST")
    assert not auth_module.api_key_scope_permits(frozenset(), "GET")


def test_api_key_scopes_map_to_least_privileged_rbac_role():
    assert auth_module.api_key_role(frozenset({"read"})) == "VIEWER"
    assert auth_module.api_key_role(frozenset({"write"})) == "ANALYST"
    assert auth_module.api_key_role(frozenset({"admin"})) == "ADMIN"
    assert auth_module.api_key_role(frozenset({"*"})) == "ADMIN"
