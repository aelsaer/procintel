# Local dev: standing up the DB and running the ΚΗΜΔΗΣ contract connector

## 1. Postgres/PostGIS

Requires Docker. In WSL, enable Docker Desktop's WSL integration first
(Docker Desktop → Settings → Resources → WSL Integration), or run this on a
host where `docker` is already available.

```bash
cd infra/docker
cp .env.example .env   # adjust if needed
docker compose up -d
```

## 2. Run the migrations

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel
./db/run_migrations.sh
```

Applies `db/migrations/*.sql` in order, then `db/marts/*.sql`
(`procurement_360` and the analytics marts).

## 3. Install the Python project

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 4. Run the tests

```bash
pytest tests/unit tests/contract        # no DB / no live API needed
DATABASE_URL=$DATABASE_URL pytest tests/integration   # real end-to-end check
```

`tests/integration` is skipped automatically if `DATABASE_URL` isn't set.

## 5. Run a backfill

```bash
# optional override; defaults to https://cerpp.eprocurement.gov.gr
export KHMDHS_API_BASE_URL=https://cerpp.eprocurement.gov.gr
python -m services.ingestion.connectors.khmdhs.cli backfill \
    --date-from 2025-01-01 --date-to 2025-01-30
    # add --resource contract (repeatable) to limit to specific resources;
    # add --no-adam-chain to skip adamChain/process-grouping resolution;
    # add --no-alerts to skip alert evaluation;
    # add --with-diavgeia (needs DIAVGEIA_API_BASE_URL too) to resolve
    # Διαύγεια decisions for every ΑΔΑ referenced — opt-in, off by default;
    # add --with-gemi (needs GEMI_API_KEY) to
    # enrich contractor entities from ΓΕΜΗ — opt-in, off by default;
    # add --with-anaptyxi (needs ANAPTYXI_API_BASE_URL too) to resolve
    # ESPA funding links via exact ΟΠΣ/MIS match — opt-in, off by default;
    # add --with-mef to look up ΜΕΦ expenses
    # for every resolved contractor and attempt tiered confidence linkage
    # to acts — opt-in, off by default
```

`KHMDHS_API_BASE_URL` defaults to the official production base URL documented
by ΚΗΜΔΗΣ (`https://cerpp.eprocurement.gov.gr`) and can be overridden for a
proxy/staging system. `DIAVGEIA_API_BASE_URL` likewise defaults to Διαύγεια's
production Open Data base (`https://diavgeia.gov.gr/opendata`) when using
`--with-diavgeia`. Same again
for `GEMI_API_KEY` if using `--with-gemi` — the base URL defaults to the
published Open Data v1 endpoint, while production access requires a personal API key
(§18.4, see `services/ingestion/connectors/gemi/README.md`). Same again for
`ANAPTYXI_API_BASE_URL` if using `--with-anaptyxi` (see
`services/ingestion/connectors/anaptyxi/README.md`). ΜΕΦ uses its published
public API by default when using `--with-mef` (see
`services/ingestion/connectors/mef/README.md`) — linkage additionally
depends on the Διαύγεια decision for a contract's ΑΔΑ already being
resolved (`--with-diavgeia`), since Tier 1/2 matching walks the
`APPROVES` link from the decision act to the originating ΚΗΜΔΗΣ act.

Raw payloads land under `./raw/<khmdhs|diavgeia|gemi|anaptyxi|mef|ted|ckan>/<resource>/...`
(local filesystem store — see `packages/source_clients/raw_store.py`);
canonical rows land in `source_records`, `entities`, `entity_identifiers`,
`entity_company_snapshots`, `entity_vies_checks`, `procurement_processes`,
`procurement_acts` (incl. `DIAVGEIA_DECISION`/`TED_NOTICE` acts),
`act_identifiers`, `act_cpv_codes`, `act_locations`, `act_parties`,
`act_links`, `process_members`, `process_merge_log`, `alert_events`,
`funding_projects`, `funding_links`, `ted_notice_details`, `mef_organizations`,
`mef_expenses`, `external_datasets`, `geo_denominators`,
`administrative_boundaries`.

## 6. Run TED (+ optionally VIES)

TED is ingested standalone — nothing on the ΚΗΜΔΗΣ side triggers it (see
`services/ingestion/connectors/ted/README.md`), so it has its own CLI:

```bash
python -m services.ingestion.connectors.ted.cli backfill \
    --date-from 2025-01-01 --date-to 2025-01-30
    # --country defaults to GR; add --with-vies (needs VIES_API_BASE_URL
    # too) to validate foreign suppliers found in matched notices
```

The connector defaults to the official public Search API v3 at
`https://api.ted.europa.eu`; `TED_API_BASE_URL` is only an optional override.
Date-window ingestion deliberately uses Search API v3. TED daily bulk packages
are keyed by OJ S issue number, so the CLI does not invent a date-based bulk URL.

Process matching (buyer ΑΦΜ + CPV + ±180-day publication-date window)
always runs — it only actually links when it finds exactly one confident
candidate ΚΗΜΔΗΣ process, so running this without any ΚΗΜΔΗΣ data ingested
yet is safe, just won't link anything.

## 7. Run CKAN (data.gov.gr enrichment datasets)

CKAN is also standalone — it's a generic data catalog, not a procurement
source, so onboarding/refreshing a dataset is its own operator action:

```bash
export CKAN_API_BASE_URL=<confirm this first — see docs/source-contracts/ckan-datagov.md>
python -m services.ingestion.connectors.ckan.cli sync-population \
    --dataset-id <ckan-dataset-slug> --reference-year 2021

python -m services.ingestion.connectors.ckan.cli sync-boundaries \
    --dataset-id <ckan-dataset-slug> --boundary-type MUNICIPALITY
```

Population (writes `geo_denominators`, behind the §22.3 "contracts per
resident" metrics) and administrative boundaries (writes
`administrative_boundaries` with real PostGIS geometry via GeoAlchemy2,
assumes the dataset's resource is GeoJSON) are implemented so far;
schools/hospitals/regional indicators/environmental layers still need
their own adapters (see `services/ingestion/connectors/ckan/README.md`).

## 8. Run the API

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel
export PROCINTEL_DEV_AUTH=true
uvicorn apps.api.main:app --reload
```

Then e.g. `curl http://localhost:8000/v1/contracts/<some ΑΔΑΜ you ingested>`.
See `apps/api/README.md` for the full endpoint list. `pytest
tests/integration/test_api_endpoints.py` (DATABASE_URL-gated) seeds a
contract and exercises every endpoint end-to-end.

## 9. Run the documents pipeline

Needs the system `tesseract` CLI (not the Python bindings):

```bash
sudo apt install tesseract-ocr
```

Greek (`ell`) tessdata is bundled directly in the repo under
`services/documents/tessdata/` (no `tesseract-ocr-ell` package needed —
`ocr.py` points `TESSDATA_PREFIX` there automatically). If you've already
set `TESSDATA_PREFIX` yourself for some other reason, that takes
precedence — make sure it includes `ell.traineddata` too, or override
`DocumentPipelineConfig(ocr_lang="eng")`.

Standalone, like TED's/CKAN's CLIs — nothing automatically discovers
document URLs to process yet:

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel
python -m services.documents.cli process \
    --url https://example.test/some-tender-document.pdf \
    --document-type TENDER_DOC
```

`pytest tests/integration/test_documents_pipeline_db.py` (DATABASE_URL-gated)
runs a hand-built PDF fixture through the full pipeline and checks the
`documents`/`document_pages`/`field_provenance` rows it writes, plus the
idempotent-reprocessing contract. `pytest tests/contract/test_documents_ocr.py`
exercises the real `tesseract` CLI (skipped automatically if it's not on
`PATH`) — English only, since Greek tessdata isn't assumed to be installed.

An existing local checkout needs `pip install -e ".[dev]"` re-run to pick
up `pypdfium2`/`pillow` (new `pyproject.toml` dependencies), and on an
**already-migrated** database, apply the new migration directly —
`psql $DATABASE_URL -f db/migrations/13_document_pages.sql` — rather than
re-running `./db/run_migrations.sh` wholesale.

## 10. Ingestion scheduling, mart refresh, and webhook retries

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel
export KHMDHS_API_BASE_URL=https://cerpp.eprocurement.gov.gr
python -m services.ingestion.orchestration.cli run-once
# or, for a single long-lived process instead of an external cron/systemd timer:
python -m services.ingestion.orchestration.cli run-forever --poll-interval-seconds 300
```

Runs the configured ΚΗΜΔΗΣ and TED ingestion jobs (see
`services/ingestion/orchestration/README.md`), then refreshes the
analytics marts, then retries any due `webhook_deliveries`. Any of the
three can be skipped with `--no-marts`/`--no-webhook-retries`. Schedule the
product workers independently when the orchestrator is not responsible for
them:

```bash
python -m services.analytics.cli refresh-marts
python -m services.analytics.cli score-queued
python -m services.alerts.cli send-digests
python -m services.alerts.cli retry-webhooks
python -m services.exports.cli
```

For real EMAIL/WEBHOOK/TEAMS/SLACK alert delivery, set the SMTP env vars
(`SMTP_HOST`/`SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_FROM_ADDRESS`)
and manage rules plus concrete delivery targets through the authenticated
API/UI. Daily and weekly rules are aggregated by `send-digests`; they are not
also sent immediately.

## 11. Auth (OIDC) and Postgres row-level security

```bash
export OIDC_ISSUER_URL=<your IdP's issuer URL>       # e.g. a self-hosted Keycloak realm
export OIDC_AUDIENCE=<the API's expected audience>
# optional: OIDC_JWKS_URL (defaults to <issuer>/.well-known/jwks.json),
# OIDC_ROLE_CLAIM (default "role"), OIDC_TENANT_CLAIM (default "tenant_id")
```

No specific OIDC provider is deployed by this repo yet. `packages/auth/` is a
generic resource server that verifies bearer JWTs against the configured
issuer. For local development only, `PROCINTEL_DEV_AUTH=true` supplies a
deterministic owner and tenant. Business profiles, workspace records, alerts,
opportunity scores, exports and entity-review actions are authenticated; shared
procurement source data remains public by design.

`db/migrations/16_row_level_security.sql` creates a restricted
`procintel_app` Postgres role with a placeholder password
(`CHANGE_ME_procintel_app`) and enables RLS on the tenant-scoped tables.
**Rotate that password before anything beyond local dev**:

```sql
ALTER ROLE procintel_app WITH PASSWORD '<a real secret>';
```

Point the API's own `DATABASE_URL` at `procintel_app` (not the
migration-owning superuser) for RLS to actually restrict anything —
connecting as the table owner/superuser bypasses RLS by Postgres design,
regardless of `FORCE ROW LEVEL SECURITY`. `tests/integration/
test_rls_enforcement_db.py` (`DATABASE_URL`-gated, plus an optional
`PROCINTEL_APP_DATABASE_URL` override) proves this end-to-end: seeds two
tenants, confirms the `procintel_app` connection only ever sees its own
`app.tenant_id`'s `alert_rules` rows.

## 12. OpenSearch full-text search

```bash
cd infra/docker && docker compose up -d opensearch
export OPENSEARCH_URL=http://localhost:9200
python -m services.search_index.cli create-index
python -m services.search_index.cli reindex-all --database-url $DATABASE_URL
```

Then `curl "http://localhost:8000/v1/search/fulltext?q=καθαρισμού"` (with
the API running, `uvicorn apps.api.main:app --reload`) — relevance-ranked
full-text search, complementary to `/v1/search`'s exact-identifier-first
Postgres search. See `services/search_index/README.md`.

`tests/integration/test_search_index_reindex_db.py` needs **both**
`DATABASE_URL` and `OPENSEARCH_URL` set — the one test in this suite
requiring two live services at once.
