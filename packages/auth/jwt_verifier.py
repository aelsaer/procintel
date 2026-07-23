"""Bearer JWT verification against an OIDC provider's JWKS endpoint.

Deliberately does not use PyJWT's built-in `PyJWKClient` — it fetches the
JWKS via `urllib.request`, not `httpx`, so it can't be exercised with the
`respx` mocking this codebase uses everywhere else for HTTP-dependent
tests. Fetching the JWKS with `httpx` directly and building the RSA
public key via `jwt.algorithms.RSAAlgorithm.from_jwk()` keeps this
consistent and testable with a fully real (not stubbed) RSA
keypair/signature in tests — see `tests/contract/test_jwt_verifier.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from .config import DEFAULT_ROLE, VALID_ROLES, OidcConfig


class JwtVerificationError(Exception):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    email: str | None
    tenant_id: str | None
    role: str


class JwtVerifier:
    def __init__(self, config: OidcConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = http_client is None
        self._keys_by_kid: dict[str, Any] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _refresh_jwks(self) -> None:
        try:
            response = await self._http.get(self._config.jwks_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise JwtVerificationError(f"failed to fetch JWKS: {exc}") from exc

        for jwk in response.json().get("keys", []):
            kid = jwk.get("kid")
            if kid:
                self._keys_by_kid[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))

    async def _get_signing_key(self, kid: str) -> Any:
        if kid not in self._keys_by_kid:
            await self._refresh_jwks()
        if kid not in self._keys_by_kid:
            raise JwtVerificationError(f"unknown key id: {kid!r}")
        return self._keys_by_kid[kid]

    async def verify(self, token: str) -> AuthenticatedUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise JwtVerificationError(f"malformed token: {exc}") from exc

        kid = header.get("kid")
        if not kid:
            raise JwtVerificationError("token header has no 'kid'")

        key = await self._get_signing_key(kid)

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=self._config.leeway_seconds,
            )
        except jwt.PyJWTError as exc:
            raise JwtVerificationError(str(exc)) from exc

        subject = claims.get("sub")
        if not subject:
            raise JwtVerificationError("token has no 'sub' claim")

        role = claims.get(self._config.role_claim, DEFAULT_ROLE)
        if role not in VALID_ROLES:
            role = DEFAULT_ROLE

        return AuthenticatedUser(
            subject=subject,
            email=claims.get("email"),
            tenant_id=claims.get(self._config.tenant_claim),
            role=role,
        )
