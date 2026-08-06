# Production release gate

Use this checklist for every staging and production release.

## Secrets

- Rotate every credential that has been shared outside the deployment secret
  store. The current GEMI key must be replaced before go-live.
- Put provider and LLM credentials only in the deployment secret store or the
  ignored `.env.production`; never in Git, images, reports, or logs.
- Keep `GEMI_RATE_LIMIT_PER_MINUTE=8`. API and scheduler must mount the same
  raw-data volume so they share the provider limiter state.

## Build and schema

```bash
npm --prefix apps/web run lint
npm --prefix apps/web run build
.venv/bin/pytest tests/unit tests/contract -q
docker compose --env-file infra/docker/.env.production \
  -f infra/docker/docker-compose.production.yml run --rm migrate
```

Do not deploy if migrations, backend tests, frontend lint/build, integration
tests, or the GitHub Actions workflow fail.

## Two-day acceptance slice

Run a fresh isolated slice before a source-contract or ingestion release:

```bash
DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel_slice \
  .venv/bin/python scripts/backfill_slice.py \
  --date-from 2026-07-28 --date-to 2026-07-29 \
  --raw-root data/slices/2026-07-28_29/raw \
  --document-root data/slices/2026-07-28_29/documents \
  --report-path artifacts/slice-2026-07-28_2026-07-29.json
```

Release requires no failed stage, no core parse failure, and no unexplained
dead enrichment. Provider outages must appear as `BLOCKED_UPSTREAM`, never as
successful empty coverage. Open `ERROR` or `BLOCKING` quality issues must be
resolved or intentionally acknowledged and quarantined from analytics.
Migration 44's partial quarantine index must be present before refreshing
large marts; otherwise eligibility checks degrade as quality history grows.

## Queue recovery

Drain each provider independently so a slow or unavailable provider cannot
hold the others. The command is resumable and respects provider budgets:

```bash
.venv/bin/python scripts/drain_enrichments.py \
  --database-url "$DATABASE_URL" \
  --provider GEMI --budget GEMI=500 --limit 500
```

Repeat for `KHMDHS_DOCUMENT`, `KHMDHS_ADAMCHAIN`, `DIAVGEIA`,
`DIAVGEIA_SEARCH`, `MEF`, and the configured `ANAPTYXI_*` periods. Keep the
daily scheduler enabled after the historical backlog has been drained.

For a Docker deployment, run the same resumable worker from the scheduler
image so it shares the raw-data volume and provider limiter with the API and
daily scheduler:

```bash
docker compose --env-file infra/docker/.env.production \
  -f infra/docker/docker-compose.production.yml run --rm scheduler \
  python /app/scripts/drain_enrichments.py \
  --raw-root /var/lib/procintel/raw \
  --provider GEMI --budget GEMI=500 --limit 500 \
  --allow-non-isolated-database
```

Run providers in separate invocations. This keeps a slow provider from
blocking document, adamChain, Διαύγεια or funding recovery and preserves the
shared ΓΕΜΗ limit of eight requests per minute.
