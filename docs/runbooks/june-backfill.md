# June Backfill Runbook

This runbook fills the local database with a first commercially useful slice:
June 2026 KHMDHS tenders/contracts/payments, optional enrichment, alert events,
analytics marts, and tenant-relative opportunity scores.

## 1. Seed A Business Profile

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel

python scripts/seed_business_profile.py \
  --tenant-name "Demo ICT Supplier" \
  --email sales@example.test \
  --cpv-prefix 72 \
  --cpv-prefix 488 \
  --nuts-code EL3 \
  --keyword λογισμικό \
  --amount-min 10000 \
  --score-now
```

The profile is stored as:

- `tenants` / `users` / `tenant_memberships`
- an active `alert_rules` row for opportunity events
- a `saved_searches` row with the same filters
- optional `opportunity_scores` if `--score-now` is passed

## 2. Dry-Run The Month Plan

```bash
python scripts/backfill_month.py --dry-run
```

Defaults:

- date range: `2026-06-01..2026-06-30`
- resources: `notice`, `auction`, `contract`, `payment`, `request`
- window size: `1` day
- resume key: `bootstrap-2026-06`
- KHMDHS adamChain and alerts enabled
- Διαύγεια direct ADA enrichment enabled
- ΜΕΦ and TED enabled through their public defaults
- ΓΕΜΗ enabled when `GEMI_API_KEY` is present
- ΑΝΑΠΤΥΞΗ enabled when its deployment URL is configured

## 3. Smoke Backfill

```bash
python scripts/backfill_month.py \
  --resource notice \
  --max-pages-per-window 1 \
  --max-records-per-window 25 \
  --khmdhs-rate 120 \
  --continue-on-error
```

This verifies network/API/DB behavior without spending a full-month request
budget.

## 4. Full June Backfill

```bash
python scripts/backfill_month.py \
  --year 2026 \
  --month 6 \
  --window-days 1 \
  --khmdhs-rate 180 \
  --diavgeia-rate 60 \
  --continue-on-error
```

Optional provider env vars:

```bash
export GEMI_API_KEY=...
export GEMI_RATE_LIMIT_PER_MINUTE=30

export ANAPTYXI_API_BASE_URL=...
export ANAPTYXI_RATE_LIMIT_PER_MINUTE=30

export MEF_RATE_LIMIT_PER_MINUTE=30

export TED_RATE_LIMIT_PER_MINUTE=60
```

`GEMI_API_BASE_URL`, `MEF_API_BASE_URL`, and `TED_API_BASE_URL` are optional
proxy/staging overrides. The production endpoints are built in. Use
`--no-mef` or `--no-ted` to exclude those public providers from a run.

The script records successful KHMDHS resource/windows in `connector_runs`.
Rerunning with the same `--resume-key` skips completed windows.

## 5. Enrich Records Already In The Database

The monthly importer enriches content when a KHMDHS record is new. If the
provider was enabled after the initial import, reconcile the existing records
in bounded batches. Local records are linked before any provider request, and
the clients enforce the configured rate limits and refresh caches.

```bash
.venv/bin/python scripts/enrich_existing.py \
  --date-from 2026-06-01 \
  --date-to 2026-06-30 \
  --provider diavgeia \
  --provider mef \
  --limit 500 \
  --offset 0 \
  --diavgeia-rate 60 \
  --mef-rate 30
```

Advance `--offset` by `500` for each batch. Use `--local-only` for a fast pass
that creates only evidence-backed links from source records already stored in
the database. Add `--provider gemi` only after `GEMI_API_KEY` is configured.

## 6. Refresh Analytics Later

```bash
python -m services.analytics.cli refresh-marts
python -m services.analytics.cli score-opportunities --all-tenants
```

## 7. Inspect The Loaded Market

```bash
python scripts/report_market_snapshot.py \
  --date-from 2026-06-01 \
  --date-to 2026-06-30 \
  --limit 20
```

This prints counts, enrichment coverage, top supplier AFMs by recorded
contract value, top CPV prefixes, and top NUTS regions.
