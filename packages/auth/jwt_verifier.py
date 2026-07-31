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
    scopes: frozenset[str] = frozenset()
    auth_method: str = "OIDC"
    mfa_verified: bool = False


class JwtVerifier:
    def __init__(self, config: OidcConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = http_client is None
        self._keys_by_kid: dict[str, Any] = {}
        self._discovered_jwks_url: str | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _refresh_jwks(self) -> None:
        jwks_url = await self._get_jwks_url()
        try:
            response = await self._http.get(jwks_url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JwtVerificationError(f"failed to fetch JWKS: {exc}") from exc

        for jwk in payload.get("keys", []):
            kid = jwk.get("kid")
            if kid:
                self._keys_by_kid[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))

    async def _get_jwks_url(self) -> str:
        if self._config.jwks_url:
            return self._config.jwks_url
        if self._discovered_jwks_url:
            return self._discovered_jwks_url

        discovery_url = self._config.issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            response = await self._http.get(discovery_url)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JwtVerificationError(f"failed to discover OIDC provider: {exc}") from exc
        discovered_issuer = str(document.get("issuer") or "").rstrip("/")
        if discovered_issuer != self._config.issuer.rstrip("/"):
            raise JwtVerificationError("OIDC discovery issuer does not match configured issuer")
        jwks_uri = document.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise JwtVerificationError("OIDC discovery document has no 'jwks_uri'")
        self._discovered_jwks_url = jwks_uri
        return jwks_uri

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

        role = self._extract_role(claims)
        raw_mfa = claims.get(self._config.mfa_claim, [])
        mfa_methods = (
            {str(value).casefold() for value in raw_mfa}
            if isinstance(raw_mfa, (list, tuple, set))
            else {str(raw_mfa).casefold()}
        )
        acr = str(claims.get("acr") or "").casefold()
        mfa_verified = bool(
            mfa_methods & {"mfa", "otp", "totp", "webauthn", "hwk", "sms"}
            or acr in {"urn:mace:incommon:iap:silver", "aal2", "aal3"}
            or acr.endswith(":2")
            or acr.endswith(":3")
        )
        if role in self._config.require_mfa_roles and not mfa_verified:
            raise JwtVerificationError(f"MFA is required for role {role}")

        return AuthenticatedUser(
            subject=subject,
            email=claims.get("email"),
            tenant_id=claims.get(self._config.tenant_claim),
            role=role,
            scopes=frozenset(str(claim) for claim in str(claims.get("scope") or "").split()),
            mfa_verified=mfa_verified,
        )

    def _extract_role(self, claims: dict[str, Any]) -> str:
        """Accept a generic role claim and Keycloak realm/client role shapes."""
        candidates: set[str] = set()

        direct = claims.get(self._config.role_claim)
        if isinstance(direct, str):
            candidates.add(direct.upper())
        elif isinstance(direct, (list, tuple, set)):
            candidates.update(str(value).upper() for value in direct)

        realm_access = claims.get("realm_access")
        if isinstance(realm_access, dict):
            realm_roles = realm_access.get("roles", [])
            if isinstance(realm_roles, (list, tuple, set)):
                candidates.update(str(value).upper() for value in realm_roles)

        resource_access = claims.get("resource_access")
        if isinstance(resource_access, dict):
            for resource in (self._config.audience, "procintel-web"):
                access = resource_access.get(resource)
                if isinstance(access, dict):
                    resource_roles = access.get("roles", [])
                    if isinstance(resource_roles, (list, tuple, set)):
                        candidates.update(str(value).upper() for value in resource_roles)

        # A token can carry several roles. Resolve to the most privileged
        # recognized application role, independently of provider ordering.
        for role in VALID_ROLES:
            if role in candidates:
                return role
        return DEFAULT_ROLE
