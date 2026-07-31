# infra/docker

PostgreSQL/PostGIS, OpenSearch, Keycloak and the persistent daily ingestion
runner.

```bash
cp -n .env.scheduler.example .env
docker compose up -d --build
docker compose logs -f ingestion-scheduler
```

Keycloak is available at `http://localhost:8080`; its Admin Console is under
`/admin`. The `procintel` realm, `procintel-web` PKCE client, API audience,
roles and branded theme are imported automatically and stored in the separate
`keycloak-postgres` volume.

For a local API and web process:

```bash
export OIDC_ISSUER_URL=http://localhost:8080/realms/procintel
export OIDC_AUDIENCE=procintel-api
export PROCINTEL_DEV_AUTH=false
uvicorn apps.api.main:app --reload

cd apps/web
OIDC_ISSUER_URL=http://localhost:8080/realms/procintel \
OIDC_CLIENT_ID=procintel-web \
OIDC_REDIRECT_URI=http://localhost:3000/callback \
npm run dev
```

The local analyst is `demo@procintel.local` / `ProcintelDemo!2026`. Replace
the bootstrap and admin passwords before using the realm beyond local
development.

`ingestion-scheduler` runs the complete ingestion cycle once per day at
`DAILY_INGEST_AT` in `DAILY_INGEST_TIMEZONE`. It waits until the scheduled
time on startup.

The database schema must already be migrated. ΓΕΜΗ stays inactive until its
key is supplied and each ΑΝΑΠΤΥΞΗ period requires a confirmed endpoint.
ΚΗΜΔΗΣ, TED, Διαύγεια, ΜΕΦ and VIES have public defaults.
