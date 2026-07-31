# ingestion/orchestration

Cursor/watermark-driven job scheduling (spec §35-36, Στάδιο 1) — no
Celery/Redis/Prefect/Dagster despite §11's recommended stack naming them;
see `scheduler.py`'s module docstring for why a Postgres-backed scheduler
(`pg_advisory_lock` for mutual exclusion, `source_cursors` for the
watermark, `connector_runs` for run history) is the deliberate MVP-scope
choice here, consistent with every other "Postgres is enough" call this
codebase has already made (`/v1/search`, the analytics marts, alerts).

| Module | Purpose |
|---|---|
| `scheduler.py` | `ScheduledJob`, rolling lookback/cursor windows, advisory locking and `connector_runs` history |
| `jobs.py` | Daily registry for ΚΗΜΔΗΣ and TED, plus explicit configuration of their targeted enrichment providers |
| `cli.py` | `run-once`, persistent `run-daily --at HH:MM --timezone ...`, and the legacy due-job polling loop |

`services/ingestion/connectors/khmdhs/scheduled.py::run_scheduled_window`
is the reference `RunWindow` implementation: all five ΚΗΜΔΗΣ resources +
`adamChain` resolution + alert firing + targeted Διαύγεια, ΓΕΜΗ, ΜΕΦ and
ΑΝΑΠΤΥΞΗ enrichment for newly affected records. TED always runs process
matching and bounded VIES validation for non-Greek suppliers.
Every provider retains its own token bucket, retries and circuit breaker.
A provider failure produces a `PARTIAL` run without discarding the primary
record or preventing the remaining providers from running.

The primary jobs recheck the most recent three days by default. This catches
late publications and source corrections; raw-content hashes and canonical
upserts make the overlap idempotent. Use `scripts/backfill_month.py` for
historical loading rather than expanding the unattended daily window.

After ingestion, the same cycle refreshes due CKAN datasets, reconciles
winner/competition facts, drains the geospatial queue, refreshes analytics
marts, recomputes opportunity scores for every tenant, rebuilds all six
non-act OpenSearch catalogs, retries pending webhooks, and removes expired
export files. Full rescoring removes stale tenant scores, including when a
profile no longer has active rules; catalog rebuilds similarly remove stale
search documents. This keeps all product tabs synchronized with fresh data.

The job-count budgets are capacity controls, not provider request rates.
Defaults are sized above the observed two-day acceptance intake
(`18,493` ΚΗΜΔΗΣ records): `12,000` adamChain lookups, `10,000` document
jobs, `12,000` geospatial jobs and `30,000` total provider jobs per cycle.
Each connector's token bucket remains the hard request-rate control, so
raising a work budget never bypasses `*_RATE_LIMIT_PER_MINUTE`.

## Run once or once per day

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel

.venv/bin/python -m services.ingestion.orchestration.cli run-once

.venv/bin/python -m services.ingestion.orchestration.cli run-daily \
  --at 02:30 \
  --timezone Europe/Athens
```

The Docker Compose service in `infra/docker/docker-compose.yml` runs the
second command with `restart: unless-stopped`. Provider endpoint overrides,
keys and rate limits are documented in
`infra/docker/.env.scheduler.example`.

## Acceptance slices and historical recovery

`scripts/backfill_slice.py` is a resumable acceptance harness for any
one- or two-day interval. The two-day limit keeps validation bounded; it
does not create date-specific ingestion behavior. It invokes the same
connectors, queues, normalization, quality checks, geography, marts,
scoring and search code used by the daily scheduler:

```bash
.venv/bin/python scripts/backfill_slice.py \
  --database-url postgresql://procintel:procintel@localhost:5432/procintel_slice \
  --date-from 2026-07-28 \
  --date-to 2026-07-29 \
  --report-path /tmp/procintel-slice-report.json
```

Use `scripts/backfill_month.py` for arbitrary historical ranges. Both
commands write durable queue state, so interruption resumes instead of
restarting or duplicating provider requests. A report verdict remains
partial while runnable backlog, missing credentials or a validated
upstream contract remains; quarantined unsafe documents are reported
separately from platform failures.

## CKAN's whole-dataset refresh (a second, deliberately different job shape)

`services/ingestion/connectors/ckan/scheduled.py::refresh_due_ckan_datasets`
scans `external_datasets` rows with `ingestion_status = 'ONBOARDED'` and
re-syncs whichever have gone stale (`last_seen_at` older than
`DEFAULT_REFRESH_INTERVAL`, currently 7 days — `update_frequency` is a
free-text column no `_sync_*` call has ever populated, so it's not
consulted yet rather than trusting an unconfirmed vocabulary), dispatching
by `adapter_name` to the matching `ckan/cli.py::_sync_*` function with its
stored `config` unpacked as kwargs. Session-scoped advisory locks
(`pg_lock.py`, keyed per `catalog_dataset_id`) give the same
mutual-exclusion guarantee as `run_due_jobs`, just without a
`source_cursors`/`connector_runs` watermark, since there's no date window
to track. A dataset must be onboarded once by hand
(`ckan/cli.py sync-population`/etc — an operator action, per that
module's docstring) before this sweep will ever pick it up.

## Operational boundary

Διαύγεια, ΓΕΜΗ, ΜΕΦ, ΑΝΑΠΤΥΞΗ and VIES are enrichment providers, not
unfiltered mirror feeds. The daily cycle queries them for ADAs, companies,
suppliers and funding references affected by new procurement records. This
avoids downloading unrelated national datasets and respects provider quotas.
Providers requiring credentials or deployment-specific endpoints are
reported as inactive until those values are configured.

`connector_runs` and `source_cursors.last_error` provide queryable run and
failure history. External paging for repeated failures remains an operations
integration rather than an ingestion concern.
