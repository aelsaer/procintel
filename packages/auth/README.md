# packages/auth

OIDC resource-server bearer-JWT verification (description.txt §40.1).
Local development uses the Keycloak realm in `infra/docker/keycloak`; the
verifier remains compatible with other standards-compliant providers.

| Module | Purpose |
|---|---|
| `config.py` | `OidcConfig.from_env()` — `OIDC_ISSUER_URL`/`OIDC_AUDIENCE` required; `OIDC_JWKS_URL`, role, tenant and MFA claims are optional overrides. |
| `jwt_verifier.py` | Discovers `jwks_uri`, verifies RS256 plus `iss`/`aud`/`exp`, reads generic or Keycloak roles, and returns the tenant identity used by RLS. |

Used by `apps/api/auth.py` as a FastAPI dependency — see that module and
`apps/api/README.md` for how a request's tenant/role context flows through
to Postgres RLS.

The browser implements Authorization Code + PKCE, proactive access-token
refresh and RP-initiated logout in `apps/web/src/lib/oidc.ts`. Hashed,
scoped API keys are handled separately by `apps/api/auth.py`.
