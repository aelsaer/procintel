"""FastAPI auth dependencies — description.txt §40.1/§39.

`get_current_user` verifies the request's bearer JWT (see
`packages/auth/jwt_verifier.py`) and returns the caller's identity/role/
tenant. `require_role` is a dependency-factory for RBAC gating on top of
that. The `JwtVerifier` is created lazily (same pattern as
`db.py::get_engine()`) so importing this module — e.g. to build the
OpenAPI schema in a test — never requires `OIDC_ISSUER_URL`/`OIDC_AUDIENCE`
to be set.
"""

from __future__ import annotations

import os

from fastapi import Depends, Header, HTTPException

from packages.auth.config import OidcConfig
from packages.auth.jwt_verifier import AuthenticatedUser, JwtVerificationError, JwtVerifier

_verifier: JwtVerifier | None = None

DEV_TENANT_ID = "00000000-0000-0000-0000-000000000101"


def dev_auth_enabled() -> bool:
    return os.environ.get("PROCINTEL_DEV_AUTH", "").lower() in {"1", "true", "yes"}


def get_verifier() -> JwtVerifier:
    global _verifier
    if _verifier is None:
        _verifier = JwtVerifier(OidcConfig.from_env())
    return _verifier


async def get_current_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser:
    if not authorization and dev_auth_enabled():
        return AuthenticatedUser(
            subject="local-owner",
            email=os.environ.get("PROCINTEL_DEV_EMAIL", "owner@procintel.local"),
            tenant_id=os.environ.get("PROCINTEL_DEV_TENANT_ID", DEV_TENANT_ID),
            role="OWNER",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization[len("Bearer ") :]
    try:
        return await get_verifier().verify(token)
    except JwtVerificationError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


def require_role(*allowed_roles: str):
    async def _check(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail=f"role {user.role!r} is not permitted to do this")
        return user

    return _check
