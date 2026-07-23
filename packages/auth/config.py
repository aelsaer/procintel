"""OIDC resource-server configuration — description.txt §40.1's "OIDC
authentication".

No specific IdP (Keycloak/Auth0/Okta/...) is chosen or assumed — this is a
generic, standards-compliant OIDC *resource server*: it verifies bearer
JWTs against whatever issuer's JWKS endpoint `OIDC_ISSUER_URL` points at,
the same way any RFC 7519/8414-compliant relying party would. Picking a
specific IdP product is a real infrastructure decision (self-hosted
Keycloak via `infra/docker/`? a managed provider?) that hasn't been made —
this module works with any of them unchanged.

`role_claim`/`tenant_claim` are configurable rather than hardcoded because
where an IdP puts app-specific claims varies by provider (some require a
URL-namespaced custom claim, e.g. `https://procintel.example/tenant_id`) —
confirm the real shape against whichever IdP is chosen, same
"confirm against the live system" posture as every ingestion connector's
`*_API_BASE_URL`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

VALID_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER", "VIEWER", "API_CLIENT")
DEFAULT_ROLE = "VIEWER"  # least privilege if the token carries no role claim


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str
    role_claim: str = "role"
    tenant_claim: str = "tenant_id"
    leeway_seconds: int = 30

    @classmethod
    def from_env(cls) -> "OidcConfig":
        issuer = os.environ.get("OIDC_ISSUER_URL")
        audience = os.environ.get("OIDC_AUDIENCE")
        if not issuer or not audience:
            raise RuntimeError(
                "OIDC_ISSUER_URL and OIDC_AUDIENCE must both be set — no default IdP is "
                "assumed. See packages/auth/README.md."
            )
        jwks_url = os.environ.get("OIDC_JWKS_URL", issuer.rstrip("/") + "/.well-known/jwks.json")
        return cls(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            role_claim=os.environ.get("OIDC_ROLE_CLAIM", "role"),
            tenant_claim=os.environ.get("OIDC_TENANT_CLAIM", "tenant_id"),
        )
