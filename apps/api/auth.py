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
import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from packages.auth.config import OidcConfig
from packages.auth.jwt_verifier import AuthenticatedUser, JwtVerificationError, JwtVerifier
from packages.domain.tables import api_keys

_verifier: JwtVerifier | None = None

DEV_TENANT_ID = "00000000-0000-0000-0000-000000000101"


def dev_auth_enabled() -> bool:
    return os.environ.get("PROCINTEL_DEV_AUTH", "").lower() in {"1", "true", "yes"}


def get_verifier() -> JwtVerifier:
    global _verifier
    if _verifier is None:
        _verifier = JwtVerifier(OidcConfig.from_env())
    return _verifier


def api_key_scope_permits(scopes: frozenset[str], method: str) -> bool:
    """Apply the API-key scope hierarchy for one HTTP method."""
    normalized_scopes = frozenset(scope.casefold() for scope in scopes)
    required_scope = "read" if method.upper() in {"GET", "HEAD", "OPTIONS"} else "write"
    return (
        required_scope in normalized_scopes
        or "admin" in normalized_scopes
        or "*" in normalized_scopes
        or (required_scope == "read" and "write" in normalized_scopes)
    )


def api_key_role(scopes: frozenset[str]) -> str:
    """Map scopes to the least-privileged RBAC role needed by dependencies."""
    normalized_scopes = frozenset(scope.casefold() for scope in scopes)
    if normalized_scopes.intersection({"admin", "*"}):
        return "ADMIN"
    if "write" in normalized_scopes:
        return "ANALYST"
    return "VIEWER"


async def _apply_subject_tenant_binding(user: AuthenticatedUser) -> AuthenticatedUser:
    """Prefer the server-side organization binding over a bootstrap JWT claim."""
    if user.auth_method != "OIDC":
        return user
    issuer = os.environ.get("OIDC_ISSUER_URL", "").rstrip("/")
    if not issuer:
        return user
    try:
        from .db import get_engine

        async with get_engine().connect() as conn:
            tenant_id = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT tenant_id
                        FROM oidc_subject_tenant_bindings
                        WHERE issuer = :issuer AND subject = :subject
                        """
                    ),
                    {"issuer": issuer, "subject": user.subject},
                )
            ).scalar_one_or_none()
        return replace(user, tenant_id=str(tenant_id)) if tenant_id else user
    except (RuntimeError, SQLAlchemyError):
        # Migration-free developer/test environments continue using the JWT claim.
        return user


async def get_current_user(
    request: Request = None,  # type: ignore[assignment]
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    if not authorization and dev_auth_enabled():
        user = AuthenticatedUser(
            subject="local-owner",
            email=os.environ.get("PROCINTEL_DEV_EMAIL", "owner@procintel.local"),
            tenant_id=os.environ.get("PROCINTEL_DEV_TENANT_ID", DEV_TENANT_ID),
            role="OWNER",
            scopes=frozenset({"read", "write", "admin"}),
            auth_method="DEV",
            mfa_verified=True,
        )
        if request is not None:
            request.state.auth_user = user
        return user
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization[len("Bearer ") :]
    if token.startswith("pk_"):
        from .db import get_engine

        token_parts = token.split("_", 2)
        if len(token_parts) != 3:
            raise HTTPException(status_code=401, detail="Invalid or expired API key")
        try:
            token_tenant_id = str(uuid.UUID(token_parts[1]))
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Invalid or expired API key") from exc
        key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with get_engine().connect() as conn:
            await conn.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": token_tenant_id},
            )
            row = (
                await conn.execute(
                    sa.select(api_keys).where(
                        api_keys.c.key_hash == key_hash,
                        api_keys.c.revoked_at.is_(None),
                        sa.or_(api_keys.c.expires_at.is_(None), api_keys.c.expires_at > datetime.now(timezone.utc)),
                    )
                )
            ).first()
            if row is None:
                raise HTTPException(status_code=401, detail="Invalid or expired API key")
            await conn.execute(
                api_keys.update()
                .where(api_keys.c.id == row.id)
                .values(last_used_at=datetime.now(timezone.utc))
            )
            await conn.commit()
        scopes = frozenset(str(scope).casefold() for scope in (row.scopes or []))
        method = request.method if request is not None else "GET"
        required_scope = "read" if method.upper() in {"GET", "HEAD", "OPTIONS"} else "write"
        if not api_key_scope_permits(scopes, method):
            raise HTTPException(
                status_code=403,
                detail=f"API key lacks required {required_scope!r} scope",
            )
        user = AuthenticatedUser(
            subject=f"api-key:{row.id}",
            email=None,
            tenant_id=str(row.tenant_id),
            role=api_key_role(scopes),
            scopes=scopes,
            auth_method="API_KEY",
            mfa_verified=True,
        )
        if request is not None:
            request.state.auth_user = user
        return user
    try:
        user = await _apply_subject_tenant_binding(await get_verifier().verify(token))
        if request is not None:
            request.state.auth_user = user
        return user
    except JwtVerificationError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


def require_role(*allowed_roles: str):
    async def _check(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail=f"role {user.role!r} is not permitted to do this")
        return user

    return _check
