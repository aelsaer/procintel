# Procurement Intelligence — Greek Public Procurement Platform

Market intelligence platform for the Greek public procurement market: connects
notices, awards, contracts, decisions, payments, funded projects, companies
and geography so a business can see who buys what, from whom, at what price,
under what funding, and when they're likely to buy again. Full product spec:
[`description.txt`](./description.txt).

## Where to start

- **Architecture**: [`docs/architecture/overview.md`](docs/architecture/overview.md)
  — pipeline, design decisions, ER diagrams, and the `procurement_360` unified
  per-procurement read model.
- **Schema reference**: [`docs/data-dictionary/canonical-schema.md`](docs/data-dictionary/canonical-schema.md)
- **Per-source field mapping**: [`docs/data-dictionary/source-mapping.md`](docs/data-dictionary/source-mapping.md)
- **Per-source API contracts**: [`docs/source-contracts/`](docs/source-contracts/)
- **Schema DDL**: [`db/migrations/`](db/migrations/) (canonical store) and
  [`db/marts/`](db/marts/) (`procurement_360`, market/buyer/supplier analytics)

## Repository layout

```
apps/            FastAPI API + Refine/Next.js procurement workspace
services/        ingestion connectors, normalization, orchestration,
                 documents, entity_resolution, linkage, analytics, alerts
packages/        shared domain types, schemas, source clients, observability
db/              migrations (canonical schema) + marts (read models)
infra/           docker, terraform, monitoring
tests/           unit, integration, contract, fixtures, golden_records
docs/            architecture, source-contracts, data-dictionary, runbooks
```

Every directory has a short README pointing at the relevant spec section.

## Status

The repository contains a working vertical slice: canonical PostgreSQL/PostGIS
storage, provider connectors, document extraction, API/search/analytics,
tenant alerts and a Refine/Next.js workspace. Competitor intelligence includes
evidence-backed bidders and winners, market-relative discovery, company
dossiers, head-to-head counts and process-level incumbent/competitor context.

Confirmed participation is deliberately separate from market inference. A
company is shown as a bidder only when an official source or a stored document
with an explicit role and ΑΦΜ supports that claim.

## Local run

From the repository root:

```bash
cd /home/projects/llmdi/procintel
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel

# Fresh database only. This creates the canonical schema, marts and seeds.
./db/run_migrations.sh

# Quota-free: derives winners and document participants from stored data.
python scripts/backfill_competition.py

# Load the Greek place gazetteer once, then queue existing acts.
python -m services.geospatial.cli load-place-gazetteer
python -m services.geospatial.cli enqueue-existing --limit 10000
python -m services.geospatial.cli worker --once --batch-size 100

# Terminal 1 (local tenant identity; use OIDC outside local development)
export PROCINTEL_DEV_AUTH=true
uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2
cd apps/web
npm install
cp -n .env.local.example .env.local
npm run dev -- --hostname 0.0.0.0 --port 3000
```

For an existing database, apply only migrations it has not received. The
current product-workflow and analytics corrections are:

```bash
for migration in \
  db/migrations/20_product_workflows.sql \
  db/migrations/21_workflow_hardening.sql \
  db/migrations/22_analytics_event_dates.sql \
  db/migrations/23_lexical_title_search.sql \
  db/migrations/24_tender_publication_details.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration"
done
```

Load or refresh the complete official OP-TED CPV 2008 catalogue before using
business-profile classification:

```bash
DATABASE_URL="$DATABASE_URL" .venv/bin/python scripts/load_cpv_catalog.py
```

The loader is idempotent and stores all 9,454 codes with Greek and English
labels. Use `--source /path/to/cpv.gc` for an offline/repeatable run.

The backfill makes no provider calls and can be rerun safely; `evidence_key`
deduplication makes it idempotent.

Geospatial ingestion is also idempotent and runs outside the source connector,
so geocoder latency never blocks new records. See
[`services/geospatial/README.md`](services/geospatial/README.md) for the
long-running worker, local-boundary path and optional provider limits.

Run the product workers from the repository root (normally from cron,
systemd, or a container scheduler):

```bash
python -m services.analytics.cli refresh-marts
python -m services.analytics.cli score-queued
python -m services.alerts.cli send-digests
python -m services.alerts.cli retry-webhooks
python -m services.exports.cli
```

## Daily ingestion

The unattended runner checks ΚΗΜΔΗΣ and TED with a short rolling overlap,
enriches affected records through every configured provider, then updates
geospatial data, competition facts, analytics, tenant opportunity scores and
pending webhook deliveries.

```bash
cd infra/docker
docker compose up -d --build ingestion-scheduler
docker compose logs -f ingestion-scheduler
```

The default schedule is `02:30 Europe/Athens`. Provider keys, endpoint
overrides and quota settings are listed in `.env.scheduler.example`. ΓΕΜΗ,
ΑΝΑΠΤΥΞΗ and VIES remain explicitly inactive until their required values are
configured.

For a manual run of the same cycle:

```bash
DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel \
  .venv/bin/python -m services.ingestion.orchestration.cli run-once
```

Verification used for the current build:

```bash
.venv/bin/pytest tests/unit tests/contract -q
DATABASE_URL=$DATABASE_URL .venv/bin/pytest tests/integration/test_product_workflows_db.py -q
cd apps/web && npx tsc --noEmit && npm run lint && npm run test:e2e
```

## Build order

Follows `description.txt` §44-45: ΚΗΜΔΗΣ ingestion (all resources) → raw
storage/versioning → `adamChain` lifecycle → canonical entities by ΑΦΜ →
direct Διαύγεια evidence retrieval → search → buyer/supplier profiles →
alerts → market analytics — then ΓΕΜΗ → ΑΝΑΠΤΥΞΗ → TED → payments →
geographic data → advanced scores. The schema in this repo already has
storage for every stage, so later stages don't require reshaping earlier work.
