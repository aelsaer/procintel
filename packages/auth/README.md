# packages/auth

Generic OIDC resource-server bearer-JWT verification (description.txt
§40.1). No specific identity provider is chosen or assumed — see
`config.py`'s module docstring.

| Module | Purpose |
|---|---|
| `config.py` | `OidcConfig.from_env()` — `OIDC_ISSUER_URL`/`OIDC_AUDIENCE` required, `OIDC_JWKS_URL`/`OIDC_ROLE_CLAIM`/`OIDC_TENANT_CLAIM` optional. `VALID_ROLES` per §39 |
| `jwt_verifier.py` | `JwtVerifier` — fetches the issuer's JWKS via `httpx` (not PyJWT's built-in `PyJWKClient`, which uses `urllib` and can't be `respx`-mocked), verifies RS256 signature + `iss`/`aud`/`exp`, returns `AuthenticatedUser(subject, email, tenant_id, role)` |

Used by `apps/api/auth.py` as a FastAPI dependency — see that module and
`apps/api/README.md` for how a request's tenant/role context flows through
to Postgres RLS.

## Not yet implemented

- Token *issuance* — there's no login flow, no IdP deployed
  (`infra/docker/` has no Keycloak/equivalent service yet). This module is
  only the resource-server (verification) side.
- API key auth (§40.2, hashed `key_prefix`/`key_hash`/`scopes`) for the
  `API_CLIENT` role — only bearer-JWT auth is implemented so far.
- Refresh-token/session handling — out of scope for a resource server;
  belongs to whichever IdP is deployed.
