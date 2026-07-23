# apps/api

FastAPI serving layer over the canonical PostgreSQL/PostGIS store and analytics
marts. OpenAPI is available at `http://localhost:8000/docs`.

## Implemented API groups

- Public procurement: exact/local search, contracts, processes, lifecycle,
  competition and similar contracts.
- Profiles: buyer, supplier/company, enriched process 360 and source evidence.
- Tenant workspace: current user, business profile and classification, saved
  searches, opportunity pipeline, stages, notes, tags and object watches.
- Alerts: rules, filters, schedules, delivery targets, inbox, delivery history,
  digest history, pause/edit/archive.
- Intelligence: tenant opportunity scores, market dashboard, CPV/NUTS markets,
  buyer/supplier intelligence, renewals, funding, relationship explorer and
  supported analytics questions.
- Operations: CSV/XLSX export jobs/downloads and owner/admin entity-match review,
  merge history and undo.

Route definitions live in `apps/api/routers`; the generated OpenAPI document is
the authoritative endpoint and schema list.

## Local run

```bash
cd /home/projects/llmdi/procintel
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel
export PROCINTEL_DEV_AUTH=true
.venv/bin/uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
```

`PROCINTEL_DEV_AUTH` creates a deterministic local owner/tenant identity and
must not be enabled in production.

## Auth and isolation

Public procurement records are intentionally shared. Tenant data is protected
through OIDC bearer authentication, RBAC dependencies and transaction-scoped
`app.tenant_id`/`app.role` PostgreSQL settings. Production must connect as the
restricted `procintel_app` role created by migration 16; the migration owner
role bypasses RLS by PostgreSQL design.

Configure production auth with `OIDC_ISSUER_URL`, `OIDC_AUDIENCE`, and optionally
`OIDC_JWKS_URL`, `OIDC_ROLE_CLAIM` and `OIDC_TENANT_CLAIM`.

## Verification

```bash
.venv/bin/pytest tests/unit tests/contract -q
DATABASE_URL=$DATABASE_URL \
  .venv/bin/pytest tests/integration/test_product_workflows_db.py -q
```

Provider-backed coverage remains source-dependent. Missing ΓΕΜΗ snapshots,
supplier identities, payments or lifecycle links are exposed as unavailable
coverage rather than inferred facts.
