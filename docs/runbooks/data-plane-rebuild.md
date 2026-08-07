# Data-plane rebuild and cutover

This runbook rebuilds canonical procurement data in parallel. The active API
continues to use `procintel` until candidate quality, enrichment and search
acceptance checks pass.

## 1. Backup and restore validation

Create a custom-format `pg_dump`, database-globals dump, raw/document file
manifests, OpenSearch index/alias snapshots and SHA-256 checksums. Restore the
dump to a disposable database and verify at least:

- migration count;
- current acts and source records;
- tenants, users and business profiles;
- saved searches, alerts, pipeline and bid workspace rows.

Never start migrations until `pg_restore` has exited successfully. Restores of
legacy databases must have a unique `schema_migrations.filename`; migration 46
enforces this invariant and refuses ambiguous duplicate history.

## 2. Candidate database

```bash
docker exec docker-postgres-1 createdb -U procintel procintel_candidate_YYYYMMDD
docker cp backup.dump docker-postgres-1:/tmp/procintel-candidate.dump
docker exec docker-postgres-1 pg_restore \
  -U procintel -d procintel_candidate_YYYYMMDD \
  --no-owner --no-privileges /tmp/procintel-candidate.dump
```

After `pg_restore` completes, apply all tracked migrations. Keep UUIDs intact;
this preserves tenant workspace references and removes the need for an unsafe
application-level remap.

## 3. Finalization

Run quality quarantine, competition facts, geospatial work, marts and tenant
scoring with the shared raw/document volumes:

```bash
docker compose -f infra/docker/docker-compose.yml run --build --rm \
  -e DATABASE_URL=postgresql://procintel:procintel@postgres:5432/procintel_candidate_YYYYMMDD \
  ingestion-scheduler python scripts/finalize_data_plane.py \
  --geospatial-limit 30000 \
  --skip-search \
  --report-path /tmp/candidate-finalize.json
```

Repeat geospatial finalization until no runnable jobs remain. `NO_LOCATION` is
a truthful terminal result, not a retryable failure.

Drain network providers in separate workers. They share provider-wide limiter
state under `$RAW_STORE_ROOT/provider-limits`, so parallel workers cannot exceed
the configured aggregate rate:

```bash
python scripts/drain_enrichments.py \
  --database-url "$CANDIDATE_DATABASE_URL" \
  --allow-non-isolated-database \
  --provider GEMI --budget GEMI=3000 --limit 3000
```

Use separate runs for ΚΗΜΔΗΣ documents/adamChain, Διαύγεια direct/search, ΜΕΦ
and each ΑΝΑΠΤΥΞΗ period. A missing or invalid upstream contract must remain
`BLOCKED_UPSTREAM`; never convert it to a successful empty result.

## 4. Search acceptance

Build candidate-only aliases first:

```bash
OPENSEARCH_INDEX_NAME=procintel_candidate_YYYYMMDD_acts \
OPENSEARCH_INDEX_PREFIX=procintel_candidate_YYYYMMDD \
python scripts/finalize_data_plane.py \
  --database-url "$CANDIDATE_DATABASE_URL" \
  --skip-quality --skip-competition --skip-marts --skip-scoring
```

The rebuild writes new physical indexes, validates all seven counts and swaps
their aliases atomically. Validation slices derive a separate namespace from
their database name automatically.

## 5. Acceptance and cutover

Do not cut over until the report and direct SQL checks agree on:

- eligible act, process, document and supplier counts;
- CPV, document and precise-geography coverage;
- enrichment queue states by provider;
- open non-placeholder `ERROR`/`BLOCKING` issues;
- mart refresh completion and tenant score counts;
- all seven OpenSearch alias counts.

Stop the scheduler, run one final short ingestion window against the candidate,
then build the production OpenSearch aliases from the candidate. Point API and
scheduler `DATABASE_URL` at the candidate and recreate only those services.
Run health, auth, search, opportunity, map, competitor and bid-workspace smoke
tests before restarting daily ingestion.

Rollback is configuration-only: stop the scheduler, restore the previous
database URL and OpenSearch aliases, recreate API/scheduler, and retain the
candidate for diagnosis. Do not delete the backup or previous physical indexes
until the agreed rollback window has expired.
