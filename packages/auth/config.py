"""OIDC resource-server configuration for FastAPI."""

from __future__ import annotations

import os
from dataclasses import dataclass

VALID_ROLES = ("OWNER", "ADMIN", "ANALYST", "SALES", "BID_MANAGER", "VIEWER", "API_CLIENT")
DEFAULT_ROLE = "VIEWER"  # least privilege if the token carries no role claim


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str | None = None
    role_claim: str = "role"
    tenant_claim: str = "tenant_id"
    mfa_claim: str = "amr"
    require_mfa_roles: tuple[str, ...] = ("OWNER", "ADMIN")
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
        return cls(
            issuer=issuer,
            audience=audience,
            # When omitted, JwtVerifier follows the provider's standard
            # discovery document and reads `jwks_uri` from there.
            jwks_url=os.environ.get("OIDC_JWKS_URL") or None,
            role_claim=os.environ.get("OIDC_ROLE_CLAIM", "role"),
            tenant_claim=os.environ.get("OIDC_TENANT_CLAIM", "tenant_id"),
            mfa_claim=os.environ.get("OIDC_MFA_CLAIM", "amr"),
            require_mfa_roles=tuple(
                role.strip().upper()
                for role in os.environ.get("OIDC_REQUIRE_MFA_ROLES", "OWNER,ADMIN").split(",")
                if role.strip()
            ),
        )
