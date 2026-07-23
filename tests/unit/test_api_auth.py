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
