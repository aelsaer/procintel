# Backend implementation progress

Plain status doc, not a design doc — if this build gets interrupted, read
this one file to know what's done and what's next without re-deriving it
from `description.txt` or the codebase. Update it at the end of every phase.

Full design context: `docs/architecture/overview.md`,
`docs/data-dictionary/`, `docs/source-contracts/`. Build-order source of
truth: `description.txt` §44-45.

## Current product status (2026-07-23)

The tenant product layer described later in this historical log is now
implemented: persisted business profiles and classification, opportunity
scoring/pipeline, saved searches, notes/tags, watches, complete alert rule and
digest workflows, exports, provenance, fuzzy entity review/merge/undo, buyer and
supplier intelligence, process tabs/similar contracts, market marts, funding,
relationship explorer, Greece/NUTS map and Refine-based workspace UI.

Migrations `17_on_demand_fetch_requests.sql` (exact-ΑΔΑΜ/ΑΔΑ search-triggered
fetch, `fetch_requests` + `apps/api/routers/fetch_requests.py` +
`services/ingestion/on_demand.py`), `18_competitor_participations.sql`
(evidence-backed bidder/winner facts, `process_participations` +
`services/competitors/participation.py`, deliberately separate from inferred
market-competitor cohorts), `19_geospatial_enrichment.sql` (place-of-performance
extraction/geocoding as its own queued worker, `services/geospatial/`, never
holding up a source connector), `20_product_workflows.sql`,
`21_workflow_hardening.sql` and `22_analytics_event_dates.sql` contain the
schema/mart additions since migration 16. The live 2026 market mart currently
exposes 4,317 contract observations and EUR 664.0M recorded net value. Missing
HHI/payment/cycle values are data-coverage signals (supplier identities and
high-confidence links are sparse), not filled with inferred facts.

`apps/api/routers/` now has 16 routers; besides the ones named above,
`business_profiles.py` (persisted profile CRUD/classification),
`entity_review.py` (candidate list/generate/review, merge undo — now writing
`audit_log` rows for `entity.merged`/`entity.split`, see below),
`evidence.py` (`/v1/evidence/{object_type}/{object_id}`, the §30.4 evidence
drawer, plus published metric methodologies), and `search_fulltext.py`
(OpenSearch-backed `/v1/search/fulltext`, separate from `/v1/search`'s
loaded-data-first exact-identifier lookup) round out the surface.

**This pass** (closing gaps found via a fresh live-database audit — 36,895
processes/34,300 contract-shaped acts but only 1 fully materialized contract
before the ΚΗΜΔΗΣ `procedureType` fix further up this log; separately, a
follow-up sweep for product/ops gaps not yet closed):
- **CKAN dataset refresh scheduling**: CKAN's onboarded datasets
  (`external_datasets`, `ingestion_status='ONBOARDED'`) are now kept fresh
  automatically. `services/ingestion/connectors/ckan/scheduled.py::refresh_due_ckan_datasets`
  is a second, deliberately different scheduler shape from
  `services/ingestion/orchestration/scheduler.py`'s date-windowed
  `ScheduledJob` — a whole-dataset refresh has no date range to compute, just
  a staleness check (`last_seen_at` vs. a fixed interval; `update_frequency`
  is unpopulated by any sync call today, so it isn't trusted yet). Wired into
  `orchestration/cli.py run-once`/`run-forever` (`--no-ckan-refresh` to skip),
  not into `jobs.py::default_jobs()`.
- **Documents pipeline now has a real caller**: `services/documents/pipeline.py::process_document()`
  had zero automatic callers before this pass — a fully built, tested
  pipeline nothing ever invoked outside its own CLI. Διαύγεια decision
  resolution (`connectors/diavgeia/resolve.py`) now runs a decision's own
  PDF (`document_url`) through it, opt-in (`--with-documents`, requires
  `--with-diavgeia`) since it's a heavier per-document cost than the
  connector's own API-response fetches. ΚΗΜΔΗΣ's own tender-attachment links
  remain unwired (no confirmed field name yet).
- **Audit logging (§40.3) now covers "login" and "entity merge/split"**:
  previously only `export`/`alert changes`/similar were audited.
  `POST /v1/workspace/login` (called once by
  `apps/web/src/lib/auth-provider.ts::procurementAuthProvider.login()`, not
  folded into `/me` to avoid flooding the log with one row per
  poll/page-load) and `entity_review.py`'s merge/undo-merge handlers now
  write `audit_log` rows (`entity.merged`/`entity.split`).
- **Real login UI + OIDC Authorization Code + PKCE flow**: `apps/web/src/app/login`
  and `.../callback` (`src/lib/oidc.ts`) — provider-agnostic (issuer
  discovered via `.well-known/openid-configuration` at login time, no
  hardcoded IdP), PKCE via the Web Crypto API (no new npm dependency), the
  resulting access token handed to the existing
  `procurementAuthProvider.login()`. Configured via
  `NEXT_PUBLIC_OIDC_ISSUER_URL`/`_CLIENT_ID`/`_REDIRECT_URI` (all three
  required together; `/login` shows a clear "not configured" message
  otherwise, since no specific IdP is deployed anywhere in this build). The
  workspace topbar gained a Σύνδεση/Αποσύνδεση control.
- **README accuracy pass**: `services/competitors/README.md` and
  `services/exports/README.md` written (neither had one).
  `services/linkage/README.md` and `services/ingestion/normalization/README.md`
  rewritten — both packages are empty placeholders; the logic they described
  actually lives per-connector (`connectors/*/resolve.py` for linkage,
  `connectors/*/normalize.py` for normalization) and each README now says so
  explicitly with a pointer table, instead of implying a shared engine exists
  where there is none.

**Analytics tab: precomputed answers to the platform's own "5 most critical
questions"**, closing the gap between "already-processed dashboards" and the
copilot's ad-hoc Q&A (identified by auditing the Market view against §27/§28/
§31.6 — buyer ranking, a real renewal list and risk indicators existed only
partially or not at all):
- **Top-buyer ranking** (`GET /v1/analytics/top-buyers`, `TopBuyerResponse`)
  — the buyer-side mirror of the pre-existing `top-suppliers`, same
  current-acts-only/taxonomy/date/geo filtering, ranked by recorded awarded
  value; §31.6 explicitly calls for both rankings, only supplier existed.
  A `BuyerLeaderboard` card sits next to the existing supplier one.
- **Upcoming-renewals pipeline surfaced in the UI** — `GET /v1/intelligence/
  renewals` already existed and returned a full list, but the Analytics tab
  only ever showed a bare count (`signals.upcoming_renewals`); a
  `RenewalsList` card now renders the real per-contract list (title,
  buyer→supplier, days to/since end date).
- **Risk and anomaly indicators (§28)** — `services/analytics/
  risk_indicators.py` (new), seven of the twelve indicator types §28 names,
  each backed by real queries (`HIGH_BUYER_CONCENTRATION`,
  `REPEAT_SAME_CONTRACTOR`, `FEW_DISTINCT_SUPPLIERS`,
  `REPEATED_MODIFICATIONS`, `LARGE_VALUE_INCREASE`,
  `UNUSUAL_AWARD_TO_CONTRACT_DELAY`, `COMPANY_INACTIVE_IN_LATER_SNAPSHOT`),
  exposed via `GET /v1/intelligence/risk-indicators`. §28's own requirement
  — non-accusatory UI copy, plus a mathematical definition, benchmark,
  minimum sample, confidence, sources and limitations per instance — is
  enforced in the dataclass shape itself, not just the frontend copy. The
  other five §28 indicator types need data/reference tables this platform
  doesn't have yet (submission deadlines, statutory thresholds) or an
  analysis method beyond a first pass (historical baselining, similarity
  clustering) — not guessed, documented in the module's own docstring.
  A `RiskIndicatorsPanel` card renders these with a confidence badge.
- Caught and fixed a real bug while writing this pass's DB-gated tests: a
  classic SQLAlchemy executemany pitfall — a multi-row `.insert()` call
  with dicts that don't all share the same keys (e.g. a `BUYER` row missing
  `amount` while the `SUPPLIER` row in the same batch has it) silently
  binds `NULL` for the missing column across the *entire* batch, not just
  the row missing it. Fixed by giving every row in a batch the same keys
  explicitly. A second, separate bug in the same tests: nesting
  `engine.connect()` inside a still-open `engine.begin()` block opens a
  second Postgres session that can't see the first (uncommitted)
  transaction's inserts — fixed by moving the query outside the insert
  transaction's `async with` block.
- New tests: `tests/integration/test_top_buyers_db.py`,
  `tests/integration/test_risk_indicators_db.py` (the three base-table
  indicators; the four mart-dependent ones aren't fixture-tested here,
  matching this codebase's existing depth for other mart-dependent
  endpoints like `market-dashboard`).

Verified: 365 unit/contract tests, TypeScript, ESLint, and 27 Playwright
scenarios (desktop + mobile = 54 runs, including the new
`e2e/auth.spec.ts` for `/login`/`/callback`). The two new DB-gated test
files (`test_top_buyers_db.py`, `test_risk_indicators_db.py`) pass against
a live database (Postgres runs in Docker — `infra/docker/docker-compose.yml`
— rather than a system-installed service; a Docker daemon restart stops
those containers, so `docker start docker-postgres-1 docker-opensearch-1`
is the fix if `localhost:5432` refuses connections after an environment
restart, not a code regression).
The broad legacy integration suite still shares database state across tests and
is not isolation-safe as one combined run; use an isolated database per suite or
the focused product integration command documented in the root README.
As of this pass, 26 of those legacy integration tests fail even when run in
isolation (ΚΗΜΔΗΣ pipeline, alerts, ΤΕΔ/ΓΕΜΗ/ΜΕΦ/CKAN/Διαύγεια resolvers,
documents pipeline) — pre-existing breakage unrelated to analytics, from
concurrent work in this same period; flagged, not fixed, in this pass.

**Map region drill-down**: clicking a NUTS region on the Analytics map
previously only updated the aggregate stats/ranking already shown beside
it — there was no way to see the actual acts behind those numbers. A new
`GET /v1/analytics/region-activity` endpoint (mirrors `/opportunities`'s
taxonomy/date/amount/geo filtering, but not restricted to opportunity act
types — a region click is about browsing everything recorded there,
contracts included) backs a new `RegionActivityPanel`, rendered below the
map (not beside it — the map+copilot row already fills that width).
Segmented control (Συμβάσεις/Προκηρύξεις/Όλα, default Συμβάσεις) plus a
single free-text box that auto-detects a numeric CPV-code vs. a keyword
search and overrides the active business-profile scope when non-empty;
clicking a different region (or asking the copilot to focus one)
refreshes the list automatically via the same `mapFocusCode` state the
region ranking already used. New DB-gated test
`tests/integration/test_region_activity_db.py` (region filtering, act-type
filtering, cross-region exclusion); extended
`e2e/workspace.spec.ts`'s existing map test rather than adding a new file,
since it already drove the same region-click flow.

## Done

- **Root-caused a real production data-completeness gap (June 2026 live
  backfill: 36,895 processes / 34,300 contract-shaped acts, but only 1 had
  a full contract payload — the other 34,299 were adamChain-discovered
  placeholders; only 1 confirmed supplier participation; 0 opportunity
  scores)**:
  - **Primary cause**: `procedureType`/`procedureCategory` from the real
    ΚΗΜΔΗΣ API is a coded `{"key","value"}` object, not a plain string —
    every `contract`-resource record hit a Pydantic `ValidationError`
    before ever reaching `upsert_act()`, so essentially the entire real
    `contract` backfill for the window silently produced nothing but
    adamChain placeholders from other resources. Fixed in `normalize.py`
    (`_key_value_str()`, extending the existing `_key_value()` helper
    already used for `organization`/`nutsCode`/`cpvItems`); 9 regression
    tests in `tests/unit/test_normalize_khmdhs_coded_fields.py`.
  - **Compounding structural bug**: `ingest_khmdhs_partition()` (and TED's
    `ingest_ted_partition()`) had no per-record isolation — one malformed
    record, or one flaky enrichment hook (adamChain/alerts/ΓΕΜΗ/... hitting
    a live API hiccup), raised out of the per-record loop and discarded
    *every other already-valid record in that page*, not just the bad one.
    Both pipelines now catch per-record and per-hook failures, record a
    bounded sample (`PartitionIngestResult.failed_records`/
    `TedPartitionIngestResult.failed_notices`), and keep going — a future
    not-yet-discovered data-shape surprise (a near-certainty against a
    live government API) will no longer sacrifice a whole page/day again.
    `khmdhs/cli.py` marks a partition with any per-record failures as
    `connector_runs.status='PARTIAL'` (an already-designed vocabulary
    value) instead of `SUCCEEDED`, so a `--resume-key` re-run correctly
    retries it instead of skipping it as done. Also fixed: blank
    `FAILED:` log lines (`httpx` timeout exceptions commonly have an empty
    `str()`) now always show the exception type name too. 6 new tests
    across `tests/unit/test_{khmdhs,ted}_pipeline_per_record_resilience.py`.
  - **Rate-limit backoff**: `retry_after=None` 429s (the common case
    observed against the real ΚΗΜΔΗΣ/Διαύγεια APIs — no `Retry-After`
    header sent) were backing off on the same 0-60s schedule as generic
    5xx errors, rarely spanning a real per-minute rate-limit window before
    `max_attempts` gave up. `packages/source_clients/retry.py` now backs
    off harder (10-90s) specifically for this case. 4 new tests.
  - **Diagnosed, not a bug**: 0 opportunity scores is because
    `score_opportunities_for_tenant()` requires an *existing, active*
    `alert_rules` row for the tenant with `event_types` containing
    `opportunity.created`/`opportunity.updated` — there's no UI/API to
    create one yet (rule creation is still "insert directly," per the
    alerts entry above), and nothing auto-creates a default catch-all one.
    `backfill_month.py`'s own `rules=0` in its printed summary already
    surfaces this; it was just easy to miss. **Needs a product decision**
    (auto-create a default catch-all rule per tenant, or keep requiring
    explicit rule setup) before scores will ever appear — not guessed here.
  - **Not a bug**: 0 ΓΕΜΗ snapshots / 0 funding projects are expected
    when `GEMI_API_KEY`/`ANAPTYXI_API_BASE_URL` aren't set (both opt-in,
    per `_resolve_provider_flags()` in `scripts/backfill_month.py`); 0
    documents is expected since nothing calls `process_document()`
    automatically yet (already documented). All of the above should
    improve substantially once the fixes above are re-run against the
    live APIs — re-running the same `--resume-key` picks up only what
    previously failed, thanks to the `PARTIAL`-vs-`SUCCEEDED` distinction.

- **Closing the actionable half of the "live-API gaps"/"scope boundaries"
  list** — four concrete items, each re-checked against this sandbox's
  actual capabilities before starting (no code action was taken on the
  genuinely-blocked items — see "Blockers" below, unchanged in kind):
  - **Greek OCR now works out of the box, no `sudo`/`apt` needed.**
    `ell.traineddata`, `eng.traineddata`, and Tesseract's `configs/`
    directory (its output-format presets — the `tsv` config `ocr.py`
    uses; a `TESSDATA_PREFIX` override needs these too, not just the
    `.traineddata` files, or `tesseract` can't resolve the `tsv` argument
    at all, discovered by hitting exactly that error while building this)
    are bundled directly under `services/documents/tessdata/` — all
    Apache-2.0 licensed, from the official tesseract-ocr/tessdata project.
    `ocr.py::_tesseract_env()` points `TESSDATA_PREFIX` there automatically
    unless the environment already sets it (an operator's own install
    always wins). `tests/contract/test_documents_ocr.py::test_run_ocr_reads_greek_text`
    runs real Greek OCR end-to-end — not gated on anything beyond
    `tesseract` being on `PATH`, and confirmed passing here.
  - **Real ClamAV client**: `services/documents/clamav.py::ClamdAntivirusScanner`
    implements the `clamd` daemon's actual INSTREAM wire protocol
    (length-prefixed chunk framing over TCP or a Unix socket, response
    parsing for `OK`/`FOUND`/`ERROR`, fail-closed on anything unparseable
    or unreachable). Not the default (`NoOpAntivirusScanner` still is) — a
    pipeline caller opts in. No `clamd` daemon is installed in this
    sandbox (confirmed: not installed, no passwordless `sudo`), so the
    real protocol was proven against a small fake in-process TCP server
    instead (`tests/contract/test_clamav_protocol.py` — genuinely real
    socket I/O and frame reconstruction, not mocked at the Python level);
    a `CLAMD_HOST`/`CLAMD_SOCKET_PATH`-gated integration test
    (`tests/integration/test_clamav_scan.py`, incl. the industry-standard
    EICAR test string) is ready for whoever has a real daemon, confirmed
    to skip cleanly here.
  - **OpenSearch incremental indexing on ΚΗΜΔΗΣ ingestion**: both
    `khmdhs/scheduled.py` (automatically, when `OPENSEARCH_URL` is set)
    and `khmdhs/cli.py backfill --with-opensearch` (manual, opt-in) now
    call `index_single_act()` after every upserted act — indexing
    failures are caught and logged, never raised, so a misbehaving
    OpenSearch cluster degrades to "search index is stale," not "ingestion
    stops." `reindex-all` (bulk backfill of the whole index) is still a
    separate, standalone manual command. Unit-tested with the real hook
    logic (`tests/unit/test_khmdhs_scheduled_opensearch_hook.py`) via a
    faked `ingest_khmdhs_partition`, not a live DB/OpenSearch/ΚΗΜΔΗΣ API.
  - **TED added to the orchestration scheduler**: new
    `connectors/ted/scheduled.py::run_scheduled_window`, the same
    date-windowed `ScheduledJob` shape as ΚΗΜΔΗΣ's, registered in
    `orchestration/jobs.py::default_jobs()` (skipped with a clear reason
    if `TED_API_BASE_URL` isn't set, same as ΚΗΜΔΗΣ's own entry). Always
    runs process matching (`resolve_notice_process_link` — TED's core
    value per that connector's own README); deliberately not the opt-in
    VIES check (needs its own confirmed base URL, same rule ΚΗΜΔΗΣ's
    scheduled job already follows for Διαύγεια/ΓΕΜΗ/ΑΝΑΠΤΥΞΗ/ΜΕΦ). CKAN
    was **not** added — its jobs are whole-dataset refreshes on their own
    cadence, not date-windowed backfills; forcing it into `ScheduledJob`'s
    shape would be the wrong abstraction, flagged as needing a second job
    shape rather than attempted here. New
    `tests/unit/test_orchestration_jobs.py` covers the registry's
    skip/register logic for both jobs together and separately.
  - **Left deliberately deferred, per explicit user confirmation**: LLM
    integration for documents (needs a real provider/API key decision),
    `apps/web` beyond its current demo path (a product-scope decision),
    and auth/RLS on more tables (no more tenant-scoped tables exist yet to
    protect — building them would be new feature scope, not closing a gap).
- **`apps/web` frontend (§11/§31)**: Next.js (App Router) + TypeScript +
  React Query, per §11's own named stack. Scaffolded via
  `create-next-app` (Next.js 16 — brand-new major version at the time of
  this pass, ships a `AGENTS.md`/`CLAUDE.md` flagging that its caching
  model changed from what any pretrained model would assume; every page
  here is a Client Component fetching via React Query specifically to
  sidestep that new "Cache Components" model entirely rather than risk a
  wrong guess about its semantics). Real pages hitting the real `apps/api`
  backend, no fabricated data: `/` (search), `/contracts/[adam]`,
  `/processes/[id]` (+ timeline), `/buyers/[id]` (+ suppliers),
  `/companies/[id]` (+ contracts) — the exact "search → process timeline →
  buyer/supplier profile" demo path named back when the original ΚΗΜΔΗΣ-
  depth-first build plan was agreed. `src/lib/api.ts` is a hand-mirrored
  typed client over `packages/schemas/responses.py` (no OpenAPI codegen
  step yet). `npm run build`/`npx tsc --noEmit`/`npm run lint` all verified
  clean in this sandbox; `next dev` itself hit this sandbox's OS inotify
  watch-limit (Turbopack's file watcher, unrelated to this code) so the
  dev server couldn't be smoke-tested end-to-end against a live API here —
  flagged explicitly in `apps/web/README.md` as the one thing to verify
  next on a normal machine. No auth/login UI (no IdP deployed), no map
  (§11 names MapLibre), no accessible component library (§11) — plain
  semantic HTML instead. Every other §31 screen (opportunity feed, watched
  buyers/competitors, alerts inbox, exports, ...) is out of scope for this
  reference path.
- **OpenSearch full-text search (§11/§29)**: `services/search_index/{config,mapping,client,document,indexer,search,cli}.py`.
  A small purpose-built `httpx` REST wrapper over OpenSearch's plain HTTP
  API (`client.py`) rather than the official `opensearch-py` SDK — that
  pulls in unused gRPC/protobuf dependencies and its transport isn't
  `httpx`-based (unmockable with `respx`, tried it, uninstalled it; see
  `pyproject.toml`'s note). `mapping.py` uses OpenSearch's built-in `greek`
  analyzer for title/buyer/supplier text fields. `indexer.py::reindex_all_acts()`
  reads `procurement_acts` (+ identifiers/CPV/locations/parties, same
  per-act query style `apps/api/queries.py` already uses) and bulk-indexes;
  `search.py::search_procurement_acts()` does relevance search
  (`multi_match` with fuzziness, CPV-prefix/NUTS filters). Wired into
  `apps/api` as `GET /v1/search/fulltext` — a **separate** endpoint from
  the existing `/v1/search`, not a replacement, so that endpoint's real,
  tested, exact-identifier-first Postgres ranking isn't put at risk by a
  backend this sandbox can't confirm is reachable. `infra/docker/docker-compose.yml`
  gained an `opensearch` service (single-node, security plugin disabled,
  dev only). Tests: pure unit tests for document-building/query-body
  construction, `respx`-mocked contract tests for every REST call
  (bulk NDJSON shape, partial-failure detection, index-exists/create),
  and `tests/integration/test_search_index_reindex_db.py` — the one test
  in this whole suite gated on **two** live services at once
  (`DATABASE_URL` and `OPENSEARCH_URL`), confirmed to skip cleanly here
  (no live OpenSearch cluster was available in this sandbox). Not yet
  wired: no ingestion hook indexes incrementally (standalone
  `reindex-all` CLI only), no document/attachment text is indexed (only
  `procurement_acts` metadata).
- **Auth (OIDC/JWT) + RBAC + Postgres row-level security (§38-40)**:
  `packages/auth/{config,jwt_verifier}.py` — a generic OIDC *resource
  server* (no specific IdP chosen; verifies bearer JWTs against whatever
  issuer's JWKS `OIDC_ISSUER_URL` points at). Deliberately fetches the JWKS
  via `httpx` rather than PyJWT's built-in `PyJWKClient` (which uses
  `urllib`, unmockable with `respx`) — tested with a genuinely generated
  RSA keypair + real signed JWTs (`tests/contract/test_jwt_verifier.py`:
  valid token, expired, wrong audience, wrong issuer, wrong signing key,
  unknown `kid`, missing role claim defaulting to `VIEWER`). `apps/api/auth.py`
  wraps this as `get_current_user`/`require_role` FastAPI dependencies.
  `apps/api/db.py::get_tenant_scoped_conn()` wraps a request in one
  transaction and calls `set_config('app.tenant_id', ...)`/
  `set_config('app.role', ...)` (bound parameters, not string-interpolated
  `SET LOCAL`) for the new `db/migrations/16_row_level_security.sql`'s RLS
  policies to key off. **Procurement data itself stays unauthenticated by
  design** — §38 explicitly calls it "shared public data"; RLS is applied
  only to the tenant-scoped tables that exist today (`alert_rules`,
  `alert_delivery_targets`, `alert_events`, `webhook_deliveries`,
  `opportunity_scores`). `GET/POST /v1/alert-rules`
  (`apps/api/routers/alert_rules.py`) is the new reference tenant-scoped,
  authenticated endpoint proving the whole chain end-to-end; every other
  router deliberately stays open. The migration also creates a restricted
  `procintel_app` Postgres role (RLS is a no-op for the table-owning
  role migrations run as, by Postgres design — a real, easy-to-miss gotcha,
  documented loudly in the migration and the runbook) with a placeholder
  password flagged for rotation before any real deployment. Tests:
  `tests/unit/test_api_auth.py` + `test_api_tenant_scoped_conn.py` (pure
  dependency logic), and `tests/integration/test_rls_enforcement_db.py`
  (`DATABASE_URL`-gated, plus an optional `PROCINTEL_APP_DATABASE_URL`
  override) — seeds two tenants' `alert_rules` and proves the
  `procintel_app` connection only ever sees its own tenant's rows, while
  confirming the superuser connection bypasses RLS entirely (the reason
  the restricted role has to exist at all).
- **Real ingestion scheduling/orchestration (Στάδιο 1, §35-36)**:
  `services/ingestion/orchestration/{scheduler,jobs,cli}.py` +
  `services/ingestion/connectors/khmdhs/scheduled.py`. Postgres-backed, not
  Celery/Redis/Prefect/Dagster — see `scheduler.py`'s module docstring for
  why that's a deliberate MVP-scope call, consistent with every other
  "Postgres is enough" decision already made this session. `source_cursors`
  and `connector_runs` (both pre-existing in DDL, unmodeled in Python until
  now) are the watermark/run-history backing; `pg_advisory_lock`
  (`packages/source_clients/pg_lock.py`, new shared helper) gives mutual
  exclusion between concurrent scheduler processes. `scheduler.compute_window()`
  is pure/unit-tested; `run_due_jobs()` (locking, cursor/connector_runs
  writes, retry-without-advancing-on-failure) is DATABASE_URL-gated,
  including a genuine two-connection lock-contention test. Only ΚΗΜΔΗΣ is
  wired into `jobs.default_jobs()` so far (all five resources + adamChain +
  alert firing per window — deliberately **not** the opt-in
  Διαύγεια/ΓΕΜΗ/ΑΝΑΠΤΥΞΗ/ΜΕΦ hooks, each needing its own confirmed API base
  URL); `cli.py run-once`/`run-forever` is the operator entrypoint (a
  cron/systemd-timer/k8s-CronJob would call `run-once`).
- **Scheduled analytics-mart refresh (§37)**: `services/analytics/refresh.py`
  + `db/migrations/14_mart_refresh_state.sql`. Refreshes all 9
  `analytics_marts.sql` materialized views in dependency order
  (`market_hhi` after `market_value_metrics`+`supplier_market_share`,
  `renewal_signals` after `cycle_time_metrics`) under one global advisory
  lock; plain `REFRESH MATERIALIZED VIEW`, not `CONCURRENTLY` (only one of
  the nine views has the unique index `CONCURRENTLY` requires — noted as a
  future per-view optimization, not attempted). `opportunity_scores`
  (§27.12) is untouched — it's a plain table needing a real scoring
  algorithm that doesn't exist yet, not a materialized view. `cli.py
  refresh-marts` standalone, or automatically as part of the orchestration
  scheduler's `run-once`/`run-forever` (`--no-marts` to skip). Tests:
  `tests/unit/test_analytics_refresh.py` (ordering/whitelist, pure) +
  `tests/integration/test_analytics_refresh_db.py` (DATABASE_URL-gated,
  confirmed to skip cleanly here).
- **Real alert delivery channels (§30.5, §32)**: `services/alerts/{email_delivery,webhook_delivery}.py`,
  extending `delivery.py`'s `DeliveryChannel` Protocol to also carry `conn`/
  `tenant_id`/`alert_event_id` (a coordinated signature change — one call
  site in `evaluate.py`, one prior implementation `LogDeliveryChannel`,
  both updated together, no backwards-compat shim needed). New
  `alert_delivery_targets` table (`db/migrations/15_alert_delivery_targets.sql`)
  supplies the concrete email address / webhook·Teams·Slack incoming URL +
  signing secret per rule per channel type — `alert_rules.delivery_channels`
  only ever named channel *types*, not destinations. `EmailDeliveryChannel`
  sends via stdlib `smtplib` (`SMTP_HOST`/`SMTP_PORT`/`SMTP_USERNAME`/
  `SMTP_PASSWORD`/`SMTP_FROM_ADDRESS`/`SMTP_USE_TLS`), wrapped in
  `asyncio.to_thread`. `WebhookLikeDeliveryChannel` is one shared
  implementation for WEBHOOK/TEAMS/SLACK: builds the full §30.5 envelope
  (event ID, idempotency key, timestamp, tenant ID, retry policy,
  HMAC-SHA256 signature in `X-Procintel-Signature`) for WEBHOOK, Microsoft's
  MessageCard shape for TEAMS, Slack's `text` shape for SLACK; writes/
  updates `webhook_deliveries` (now modeled in `packages/domain/tables.py`
  too — pre-existing DDL, unmodeled until now), dedup on `(tenant_id,
  idempotency_key)`, first attempt synchronous, `retry_pending_deliveries()`
  sweeps due `PENDING` rows with exponential backoff (60s × 2^attempt,
  capped 6h, `FAILED` after 8 attempts). `MultiplexingDeliveryChannel` fans
  out to every configured real channel; one channel's failure (logged) never
  blocks the others. `services/alerts/cli.py retry-webhooks`, also wired
  into the orchestration scheduler's `run-once`/`run-forever`
  (`--no-webhook-retries` to skip). Tests: pure envelope/backoff/signature
  unit tests, respx-mocked webhook-POST and monkeypatched-`smtplib`
  contract tests, and a DATABASE_URL-gated integration test exercising the
  full `evaluate_and_fire` → `MultiplexingDeliveryChannel` →
  real-`webhook_deliveries`-row path plus the retry sweep's success and
  give-up-after-max-attempts cases.
- **Documents pipeline (§23/§24)**: `services/documents/{config,storage,mime,
  download,antivirus,pdf_text,ocr,amounts,entities,db_writer,pipeline,cli}.py`.
  Full flow implemented: streamed download with a size cap enforced mid-stream
  (`download.py`, §23.2) → magic-byte MIME sniffing, not a trusted header
  (`mime.py`) → SHA-256 → antivirus scan (`antivirus.py` — a `Protocol` +
  `NoOpAntivirusScanner`, mirroring the `DeliveryChannel`/`RawStore` pattern;
  **no real ClamAV wired up**, flagged) → content-addressed original storage
  (`storage.py`, separate from `packages/source_clients/raw_store.py` since
  documents are binary blobs keyed by hash, not dated JSON partitions) →
  per-page text-layer detection via `pypdfium2` (new dependency, together
  with `pillow`), OCR fallback only for pages below a configurable
  usable-text-layer threshold, via the system `tesseract` CLI through
  `subprocess` (`ocr.py` — ported the *pattern*, not the code, from
  `receiptx_v3_bundle/receiptx_v3/ocr.py:46-101` elsewhere in
  `/home/projects/llmdi`; that project's own PaddleOCR/Surya paths are
  broken there and live in an incompatible Python 3.10 env regardless) →
  Greek amount parsing handling all four spec-named formats (`amounts.py`,
  §23.4 — European/space-grouped/symbol-first/US-style, disambiguated by a
  single regex requiring exactly-3-digit thousands groups vs. a 1-2 digit
  decimal tail, no locale assumption needed) → regex entity extraction for
  ΑΔΑ/ΑΔΑΜ/ΑΦΜ/CPV/MIS-OPS/dates/protocol numbers/duration/lot
  numbers/units of measurement/IBAN (`entities.py`, §23.3 — ΑΦΜ reuses
  `connectors/khmdhs/afm.py::valid_greek_afm` as-is; IBAN is opt-in via
  `DocumentPipelineConfig.extract_iban`, off by default per "μόνο όπου
  επιτρέπεται") → idempotent writes to `documents`/`document_pages`
  (new migration `13_document_pages.sql`, tsvector/GIN full-text index,
  Postgres-is-enough-for-MVP consistent with `/v1/search`)/`field_provenance`
  (`db_writer.py` — dedup key is `documents.sha256`, same
  content-hash-dedup discipline every connector uses). Every extracted
  field's `field_provenance.confidence` is scaled down by the page's OCR
  mean-confidence when the page needed OCR, and a failed-ΑΦΜ-checksum
  extraction is still recorded (per §7.2) at a visibly lower confidence
  rather than dropped. ΑΔΑ/ΑΔΑΜ regex shape is inferred from real-world
  examples, not a confirmed spec format — description.txt §7.2 gives
  normalization *rules* (uppercase/trim/never-fuzzy-match) but no
  character-count/regex, flagged in `entities.py`'s module docstring.
  Tests: `tests/unit/test_documents_{amounts,entities,mime,storage}.py`,
  `tests/contract/test_documents_{pdf_text,ocr,download}.py` (the OCR
  contract test runs the *real* `tesseract` CLI against a hand-rendered
  image, skipped if `tesseract` isn't on `PATH`; two hand-built PDF fixtures
  in `tests/fixtures/documents/` cover both the text-layer path and the
  image-only/OCR-fallback path — the latter embeds a real JPEG via
  `DCTDecode` with no text operators at all), and
  `tests/integration/test_documents_pipeline_db.py` (`DATABASE_URL`-gated,
  confirmed to skip cleanly here — full pipeline run + idempotent
  re-run assertion). **Not yet wired to any connector**: nothing calls
  `process_document()` automatically for a discovered document URL yet
  (Διαύγεια's `document_url`, ΚΗΜΔΗΣ tender attachments) — it's a
  standalone CLI (`python -m services.documents.cli process --url ...`),
  same posture TED's and CKAN's CLIs already have.
- **Cross-connector integrity fix (found while verifying "are the connectors
  actually fully connected")**: `adamchain.py` and `diavgeia/resolve.py`
  wrote process membership to `process_members` (the audit trail) but never
  to `procurement_acts.process_id` — the denormalized pointer every single
  CTE in `db/marts/procurement_360.sql` filters and groups on. Membership
  was correctly recorded but invisible through the primary read path: the
  whole mart (acts, parties/suppliers, documents, Διαύγεια decisions, ...)
  would silently return empty for any process, even a fully-linked one.
  Fixed in both places (`procurement_acts.process_id` now kept in sync with
  `process_members` on every assignment and every merge repoint); regression
  assertions added to `test_khmdhs_adamchain_db.py` and
  `test_diavgeia_resolve_db.py` checking `procurement_acts.process_id`
  directly, not just `process_members`. Follow-up closed:
  `tests/integration/test_khmdhs_cli_composed_db.py` calls `cli.py`'s
  `_run_backfill()` itself (not the individual resolvers) with
  `--with-diavgeia --with-gemi` both on, and asserts adamChain + alerts +
  Διαύγεια + ΓΕΜΗ all fired correctly off the same ingestion run, including
  the ΓΕΜΗ cache gate collapsing two records sharing one supplier ΑΦΜ into a
  single real API call.
- Canonical schema: `db/migrations/01-09` (the original 9 files) +
  `10_funding_links_review.sql`, `11_gemi_lexicons.sql`, `12_facilities.sql`
  (added later, see the ΑΝΑΠΤΥΞΗ/ΓΕΜΗ/CKAN entries below),
  `db/marts/procurement_360.sql`, `db/marts/analytics_marts.sql`. Verified
  column-consistent, no dangling FKs. `db/run_migrations.sh` now also runs
  `db/seeds/*.sql` (a new final step — previously nothing loaded seeds at
  all); `db/seeds/gemi_lexicons.sql` is the first real seed file.
- Local dev infra: `infra/docker/docker-compose.yml` (Postgres+PostGIS),
  `db/run_migrations.sh`.
- Connector shared primitives: `packages/source_clients/{base,rate_limit,retry,raw_store}.py`.
- `packages/domain/tables.py`: SQLAlchemy Core tables for `source_records`,
  `external_datasets`, `entities`, `entity_identifiers`,
  `entity_company_snapshots`, `entity_vies_checks`, `procurement_processes`,
  `procurement_acts`, `act_identifiers`, `act_parties`, `act_cpv_codes`,
  `act_locations`, `act_links`, `process_members`, `process_merge_log`,
  `tenants`, `users`, `alert_rules`, `alert_events`, `funding_projects`,
  `funding_links`, `ted_notice_details`, `mef_organizations`,
  `mef_expenses`, `geo_denominators`, `administrative_boundaries`,
  `facilities`. Verified column-consistent against `db/migrations/*.sql`
  (exact match on every table, re-checked after each addition — 27 tables
  total now). `administrative_boundaries.geom`/`facilities.geom` use
  GeoAlchemy2's `Geometry` type (`geoalchemy2` added as a new dependency,
  `pyproject.toml`); `act_locations.geom` is still omitted since the ΚΗΜΔΗΣ
  connector never writes it. `gemi_legal_forms`/`gemi_company_statuses`
  (§18.2's reference vocabulary) deliberately **not** modeled here, same as
  `nuts_areas`/`cpv_codes` — pure reference tables nothing in the Python
  app queries via SQLAlchemy, populated only via `db/seeds/*.sql`.
- ΚΗΜΔΗΣ connector, **all five resources + adamChain/process grouping**:
  `services/ingestion/connectors/khmdhs/{afm,config,client,normalize,db_writer,adamchain,pipeline,cli}.py`.
  Idempotent (content-hash dedup, upsert-by-ΑΔΑΜ), rate-limited, retrying.
  Buyer/supplier entities resolved by exact ΑΦΜ. `client.fetch_resource_page`,
  `normalize.normalize_khmdhs_record`, `db_writer.upsert_act`/`ingest_khmdhs_record`
  are the shared, resource-parameterized entry points (old contract-only
  names kept as thin backward-compat wrappers). `db_writer.upsert_act` now
  returns `ActUpsertResult` (act id/type, is_new, changed material fields) —
  not a bare id — so both adamChain resolution and alert evaluation can react
  to it. `adamchain.resolve_adam_chain_for_act` links related acts and
  assigns/merges `procurement_processes`. Both it and alert evaluation are
  wired into `pipeline.py` via one `on_ingest_result(conn, resource,
  IngestResult)` hook, composed in `cli.py` (on by default; `--no-adam-chain`
  / `--no-alerts` to skip either). Process merges are audited and reversible
  (`process_merge_log`); merged-away `public_id`s stay resolvable.
- `services/entity_resolution/resolve.py`: shared exact-ΑΦΜ resolution
  (§8 level 2), source-agnostic, used by the ΚΗΜΔΗΣ connector and ready for
  ΓΕΜΗ/others to reuse unchanged. Candidate blocking/scoring/review-queue
  (§25) not yet built — deferred until a second identity source needs it.
- `apps/api`: FastAPI app, 9 routes (see Phase D below). `apps/api/db.py`
  creates its engine lazily so importing the app never needs `DATABASE_URL`.
  `/v1/search` uses **true keyset pagination**, not the earlier OFFSET-based
  approach: exact-identifier (ΑΔΑΜ/ΑΔΑ) matches are front-loaded onto page 1
  only, and every cursor advances the title-match phase on
  `(normalized_title, id)`. This fixed a real correctness bug in the old
  version — re-running the unpaginated exact-match query on every page
  while advancing title-match OFFSET independently could duplicate or skip
  rows once the two result sets interacted across a page boundary. An edge
  case is handled explicitly: if exact matches alone fill a page, a 1-row
  peek (not included in that page) determines `has_more`/`next_cursor`
  rather than assuming the title phase is empty.
  `tests/integration/test_api_search_pagination_db.py` seeds 5 title-only
  rows and walks all pages at `limit=2`, asserting no duplicates/gaps and
  the correct final `has_more=False`.
- `services/alerts/{evaluate,delivery}.py`: **8 of 9 §30.5 event types**,
  all four `evaluate_*_and_fire()` functions sharing one rule-matching +
  dedup-insert + deliver core (`_fire_event()`) — active `alert_rules`
  (cpv_prefix/buyer_id/supplier_id/amount range filters) matched against a
  changed object, deduplicated `alert_events` writes (`INSERT ... ON
  CONFLICT DO NOTHING` on the DB's unique index), delivery via
  `DeliveryChannel` (only `LogDeliveryChannel` implemented — real
  email/webhook/Teams delivery needs infra not built here).
  `evaluate_and_fire()` now derives event type from the act's own
  `act_type` (`_EVENT_TYPES_BY_ACT_TYPE`): `REQUEST`/`NOTICE` ->
  `opportunity.created`/`opportunity.updated`, `CONTRACT` ->
  `contract.created`/`contract.modified` (as before), `PAYMENT` ->
  `payment.detected` (one event type either way — no
  "payment.modified" in §30.5); `AWARD` acts deliberately produce nothing
  (no §30.5 mapping). Three more event types are produced outside the
  act-upsert hook, since they're not procurement_acts-keyed:
  `evaluate_company_status_change_and_fire()` (wired into
  `connectors/khmdhs/cli.py` right after
  `gemi/resolve.py::resolve_company_snapshot()` — which now returns a
  `SnapshotResolveResult(wrote_new_snapshot, old_status, new_status)`
  instead of a bare bool, so the caller can tell a *status* change from
  any other field changing; fires only on a genuine status transition,
  never on a brand-new company's first snapshot); `evaluate_buyer_new_procurement_and_fire()`
  (wired into `adamchain.py::_assign_process()`, which now returns
  `(process_id, is_new)` instead of a bare UUID — `resolve_adam_chain_for_act()`
  gained an **optional** `delivery_channel` parameter, defaulting to
  `None`/off specifically so every pre-existing caller keeps working
  unchanged; fires only when a genuinely *new* `procurement_processes` row
  is created, never on an extension or merge); `evaluate_expiring_contracts_and_fire()`
  (§30.5's `contract.expiring` — the one event type nothing triggers
  automatically; a time-based scan a periodic caller would invoke,
  `as_of` folded into the material-change hash so a contract nearing
  expiry gets a fresh reminder each day rather than one dedup'd-away
  alert). This also closed a real, separate gap: ΚΗΜΔΗΣ's
  `procurement_acts.end_date` was never populated by anything —
  `normalize.py`/`db_writer.py` now map a best-effort-guessed
  `contractEndDate`/`endDate`/`contractDurationEndDate` field into it
  (§27.11's renewal-window logic already assumed this field existed;
  §16's field list just never named it), and `end_date` was added to
  `_MATERIAL_ACT_FIELDS` too. **Not implemented, on purpose**:
  `alert.triggered` — reads as the delivery-envelope concept itself
  (event ID, idempotency key, retry policy, signature), which
  `DeliveryChannel.deliver()` already stands in for, not a new source
  condition to detect.
- Διαύγεια connector, **direct ΑΔΑ fetch + SEARCH + ADVANCED_SEARCH +
  signers** (§17.3, §6.3): `services/ingestion/connectors/diavgeia/{config,client,normalize,db_writer,resolve}.py`.
  `client.py` tracks per-capability status (`DIRECT_ADA_FETCH`/`SEARCH`/
  `ADVANCED_SEARCH`, others still `UNKNOWN`/not implemented) with
  **separate circuit breakers per capability** — a degraded SEARCH/
  ADVANCED_SEARCH must never block the others (§17.3's explicit
  requirement; a shared breaker would have violated it).
  `resolve.py::resolve_decision_for_ada()` fetches by direct ΑΔΑ, stores as
  its own `DIAVGEIA_DECISION` act, links it to the originating ΚΗΜΔΗΣ act
  (`act_links`, `APPROVES`/`EXACT_ADA`/confidence 1.0), and joins the
  origin act's `procurement_processes` if one exists.
  `resolve.py::resolve_decision_via_search()` implements §17.4's fallback
  tier ("search by title or organization", `DIAVGEIA_SEARCH_MATCH`,
  confidence 0.75 < 1.0) for when direct fetch finds nothing — requires
  **both** an organization-label match and a title match above threshold
  (via the new shared `services/entity_resolution/text_similarity.py`) on
  a single unambiguous candidate; when SEARCH alone yields zero/multiple
  candidates and a `decision_type`/`protocol_number` is available, one
  ADVANCED_SEARCH retry narrows before giving up (a disambiguation
  narrower, not an independent linkage tier — §17.4 only describes one
  confidence for this). Wired into the ΚΗΜΔΗΣ CLI as `--with-diavgeia`
  (direct fetch, **opt-in**, needs its own unconfirmed base URL) and
  `--with-diavgeia-search` (search fallback, needs `--with-diavgeia` too,
  attempted only when direct fetch found nothing). Triggered by
  `ActUpsertResult.related_ada` (direct) / the act's own title + buyer name
  fetched from the DB (search).
  **Signers** (§6.3's explicit exception to "never create an entity from a
  name alone"): decision signer names become (or are matched to) `PERSON`
  entities via `db_writer.py::_find_or_create_person_entity()` (dedup on
  normalized name only — a known weaker identity guarantee than everywhere
  else, since no identifier exists for a signer), linked via
  `act_parties(party_role='SIGNER_PERSON')`, replaced wholesale on every
  upsert. The issuing authority/organizational unit are deliberately *not*
  given this treatment — still plain text, unchanged from before.
  **Real bug caught and fixed while building this**: ΚΗΜΔΗΣ's `db_writer.py`
  used to attach `related_ada` values as `act_identifiers(scheme='ADA')` on
  the ΚΗΜΔΗΣ act itself — wrong, since that identifier belongs to the
  Διαύγεια decision act, and `act_identifiers` has a global unique index on
  (scheme, value). The old code would have silently overwritten the ΚΗΜΔΗΣ
  act's title/date with the decision's the moment this connector tried to
  create the real decision act. Fixed before wiring Διαύγεια in — see
  `db_writer.py`'s module docstring and
  `connectors/khmdhs/README.md`. `related_ada` is now exposed on
  `ActUpsertResult` purely as a trigger list, never written as an
  identifier of the ΚΗΜΔΗΣ act.
- ΓΕΜΗ connector, **find-by-ΑΦΜ enrichment + attribute search**:
  `services/ingestion/connectors/gemi/{config,client,provider,normalize,db_writer,cache,resolve}.py`.
  `provider.py` implements description.txt §18.4's exact
  `CompanyRegistryProvider` Protocol, so the enrichment flow (`resolve.py`)
  doesn't depend on `GemiClient` directly or on ΓΕΜΗ API key approval —
  swappable later. `search(CompanySearchQuery)` (name/kad/status/prefecture/
  municipality, §18.4's parameter list) is now implemented — `client.py`'s
  `search()` + `provider.py`'s normalization of each result — though it has
  no caller yet in the ΑΦΜ-triggered enrichment flow itself; it's a
  capability for future use (entity-resolution disambiguation, a manual
  lookup UI), not something this pass wires into the pipeline. `db_writer.py`
  writes true temporal snapshots to
  `entity_company_snapshots` (§18.2: only a new row when company-relevant
  fields actually differ, closing out the previous one — never overwrite).
  **`lexicon.py`** (new): `LEGAL_FORM_LEXICON`/`COMPANY_STATUS_LEXICON` —
  real Greek company-law/ΓΕΜΗ vocabulary (ΑΕ/ΕΠΕ/ΙΚΕ/.../ACTIVE/SUSPENDED/
  ..., accent-insensitive lookup via NFD-decomposition; a real bug caught
  and fixed by my own test — `.upper()` alone doesn't strip Greek τόνοι, so
  "Ιδιωτική" and "ΙΔΙΩΤΙΚΗ" didn't match until accent-stripping was added).
  `normalize.py` now populates `legal_form_code`/`company_status` from this
  lexicon instead of passing raw labels through — closes a real bug where
  equivalent spellings (e.g. "ΕΝΕΡΓΗ" vs "ΕΝ ΕΝΕΡΓΕΙΑ") could spuriously
  look like a material change to `db_writer.py`'s snapshot-diff. Mirrored
  as queryable reference tables (`db/migrations/11_gemi_lexicons.sql` +
  `db/seeds/gemi_lexicons.sql`) — keep both representations in sync by
  hand if either changes. `cache.py::should_refresh()` implements §18.3's
  policy (new → immediate; stable status → 30-day; in-transition/negative
  → 7-day, never permanent), `is_stable_status()` now checking
  `lexicon.STABLE_STATUSES` (canonical codes only) instead of a raw-label
  whitelist, gated on `source_records` history, not wall-clock mocking.
  Wired into the
  ΚΗΜΔΗΣ CLI as `--with-gemi` (opt-in, needs `GEMI_API_BASE_URL` +
  `GEMI_API_KEY`), triggered by `ActUpsertResult.contractor_entity_id`/
  `contractor_afm_normalized`.
- ΑΝΑΠΤΥΞΗ connector, **all 3 programming periods, join hierarchy Levels
  1-4**: `services/ingestion/connectors/anaptyxi/{config,client,normalize,db_writer,resolve}.py`.
  `config.py` now has a separate base-URL env var **per programming
  period** (§19.3 — `ANAPTYXI_2007_2013_API_BASE_URL`/
  `ANAPTYXI_2014_2020_API_BASE_URL`/`ANAPTYXI_2021_2027_API_BASE_URL`, each
  very likely a separate deployment, not a query parameter on one system);
  `ANAPTYXI_API_BASE_URL` unsuffixed is a backward-compatible alias for
  2014-2020. `resolve.py::resolve_funding_link_for_act()` tries all 4
  levels strictly in order, stopping at the first that yields exactly one
  unambiguous candidate: **Level 1** exact ΟΠΣ/MIS (`MIS_OPS_EXACT`, 0.95 —
  tries both ΚΗΜΔΗΣ candidate funding-reference fields,
  `publicFundingRefOps`/`espaFundProgramRef`, recording which one actually
  matched — the §19.4 "critical correction"); **Level 2** beneficiary/
  contractor ΑΦΜ + title + period (`AFM_TITLE_PERIOD`, 0.85); **Level 3**
  ΑΔΑ/ΑΔΑΜ found in the candidate's own metadata (`ADA_ADAM_IN_METADATA`,
  0.90); **Level 4** looser title + amount + region fuzzy match
  (`FUZZY_TITLE_AMOUNT_REGION`, 0.60, left with `funding_links.reviewed_by
  IS NULL` — the review-queue signal, mirroring `act_links.reviewed_by`'s
  existing convention; a new migration,
  `db/migrations/10_funding_links_review.sql`, added that column since
  `funding_links` didn't have it before). Levels 2-4 share **one**
  beneficiary/contractor-ΑΦΜ-scoped search call
  (`client.find_projects_by_beneficiary_afm`) rather than three separate
  ones — ΑΝΑΠΤΥΞΗ's API isn't known to expose a general full-text/region
  search independent of ΑΦΜ. Title/period/ΑΔΑ/amount/region matching uses
  the new shared `services/entity_resolution/text_similarity.py`
  (stdlib `difflib`, no fuzzy-matching dependency). Beneficiary entity
  resolved by exact ΑΦΜ via the same shared `services/entity_resolution`.
  Wired into the ΚΗΜΔΗΣ CLI as `--with-anaptyxi --anaptyxi-period <period>`
  (opt-in, needs the matching base-URL env var), triggered by
  `ActUpsertResult.funding_ref_candidates` (Level 1) and/or
  `contractor_afm_normalized`/the act's own buyer ΑΦΜ (Levels 2-4) —
  `cli.py` fetches the act's title/date/amount/region from the DB since
  `ActUpsertResult` doesn't carry them directly.
- TED connector, **Search API v3, join hierarchy Levels 3-4**:
  `services/ingestion/connectors/ted/{config,client,normalize,db_writer,resolve,pipeline,cli}.py`.
  Direction is the mirror image of Διαύγεια/ΓΕΜΗ/ΑΝΑΠΤΥΞΗ: nothing on the
  ΚΗΜΔΗΣ side names a TED notice up front, so TED runs its own standalone
  backfill (`pipeline.py`/`cli.py`, not triggered by ΚΗΜΔΗΣ) and then
  *searches from the TED side* for a matching ΚΗΜΔΗΣ process
  (`resolve.py::resolve_notice_process_link()`). **Level 3** (buyer ΑΦΜ +
  CPV overlap + ±180-day publication-date window, confidence 0.85) is
  tried first; if it finds zero or multiple candidates, **Level 4** (same
  buyer, no CPV requirement — title similarity + ±15% amount tolerance
  instead, via `services/entity_resolution/text_similarity.py`, confidence
  0.65) is tried as a fallback. Both link only when the query returns
  exactly one distinct candidate process; Level 4 links are left with
  `act_links.reviewed_by IS NULL` (`act_links` already had this column —
  no migration needed here, unlike ΑΝΑΠΤΥΞΗ's `funding_links`).
  Date-window backfills use the official `POST /v3/notices/search` contract.
  `normalize.py::parse_bulk_xml_package()` remains available and tested for
  operator-supplied XML, but no date-based bulk transport is exposed: TED
  daily packages are keyed by OJ S issue number rather than calendar date.
  `normalize.py::_detect_eforms_version()` implements §21.2's
  version-awareness requirement as an explicit-marker/confidence gradient
  rather than a real eForms-A-vs-B distinction (no samples of either were
  available). Buyer/supplier resolution splits by country: Greek parties
  reuse the shared exact-ΑΦΜ resolver, non-Greek parties get their own
  `entity_identifiers(scheme='EU_VAT')` lookup — Greek checksum validation
  doesn't apply to a German or French VAT number. Levels 1-2 remain
  unreachable without the documents pipeline (ΚΗΜΔΗΣ's field list carries
  no TED reference at all).
- VIES connector, **`checkVat` only**:
  `services/ingestion/connectors/vies/{config,client,db_writer,resolve}.py`.
  SOAP request/response (`client.py`), append-only `entity_vies_checks` per
  check (never a company profile, §3.9). No caching policy (unlike ΓΕΜΗ's
  explicit §18.3) — every call is a real check. Triggered only from TED's
  `cli.py` (`--with-vies`) when a matched-or-unmatched notice's supplier has
  `country_code != 'GR'` — the only place this codebase currently produces
  a non-Greek supplier.
- ΜΕΦ connector, **find-by-recipient-ΑΦΜ + 4-tier confidence linkage**:
  `services/ingestion/connectors/mef/{config,client,normalize,db_writer,resolve}.py`.
  `client.py::find_expenses_by_recipient_afm()` (endpoint path/params
  TODO-flagged, no sample payload was available). `db_writer.py` stores
  every returned expense idempotently (content-hash dedup) and
  finds-or-creates its `mef_organizations` row, always leaving
  `linked_act_id`/`link_method`/`confidence` NULL — matching is
  `resolve.py`'s job. `resolve.py::resolve_expense_link()` implements
  §20.2's tiered combination, **never** ΑΦΜ alone: same ΑΔΑ + same ΑΦΜ
  (0.99, `ADA_AND_AFM`) > same ΑΔΑ + same buyer (0.97, `ADA_AND_BUYER`) >
  same ΑΦΜ + same amount + ±5 days (0.90, `AFM_AMOUNT_DATE`) > same ΑΦΜ
  only (candidate, no link row at all — every expense here already matches
  by ΑΦΜ since that's the search precondition, so "no tier matched" simply
  means "not a link"). **Real modeling issue caught while designing Tier
  1/2**: an ΑΔΑ only ever identifies a Διαύγεια *decision* act
  (`act_identifiers`), and decision acts carry no `act_parties` of their
  own (`diavgeia/db_writer.py`: "Decisions get no act_parties in this
  pass") — so Tier 1/2 can't compare the contractor/buyer against the
  decision act directly. Fixed by resolving the ΑΔΑ to its decision act,
  then following the `APPROVES` `act_links` edge (written by
  `diavgeia/resolve.py`) to the originating ΚΗΜΔΗΣ act, and comparing
  parties there instead (`resolve.py::_find_origin_act_for_ada()`). Wired
  into the ΚΗΜΔΗΣ CLI as `--with-mef` (opt-in, needs `MEF_API_BASE_URL`),
  triggered by `ActUpsertResult.contractor_entity_id`/
  `contractor_afm_normalized` (same trigger as ΓΕΜΗ) — and Tier 1/2 linkage
  additionally depends on `--with-diavgeia` having resolved the relevant
  ΑΔΑ first, since that's what creates the `APPROVES` edge this connector
  walks.
- CKAN / data.gov.gr connector, **all six §22.2 dataset types**:
  `services/ingestion/connectors/ckan/{config,client,registry,normalize,geo,boundaries,facilities,db_writer,cli}.py`.
  Unlike the other 8 sources, CKAN is a generic data catalog, not a
  procurement source, so it's shaped differently: `client.py` implements
  the three Action API operations description.txt names explicitly
  (`package_search`/`package_show`/`resource_search`) generically for any
  dataset, plus a plain resource downloader; `registry.py` upserts every
  onboarded dataset into `external_datasets` (idempotent on
  `(catalog_source, catalog_dataset_id)`); each dataset then needs its own
  adapter for its own file format/fields.
  **Population + regional economic indicators** (`normalize.py::normalize_metric_csv`,
  `db_writer.py::ingest_metric_dataset`) write `geo_denominators` behind
  the §22.3 per-capita metrics — population
  (`normalize_population_csv`/`ingest_population_dataset`) is now a thin
  wrapper over the generic form, fixed to the population-specific metric
  name/value column; any other indicator (GDP per capita, unemployment
  rate, ...) uses the generic form directly with its own `metric_name` and
  value column, dedup-scoped per metric so different indicators for the
  same dataset never collide on content hash.
  **Administrative boundaries** (`geo.py`/`boundaries.py`) writes
  `administrative_boundaries` rows with real PostGIS geometry:
  `geoalchemy2` was added as a genuinely new project dependency
  specifically for this (`packages/domain/tables.py`'s
  `administrative_boundaries.geom` column) — the first slice in this
  codebase to write geometry rather than just carry a `nuts_code` string.
  `geo.py::geojson_to_multipolygon_wkt()` hand-converts GeoJSON
  `Polygon`/`MultiPolygon` coordinate arrays to WKT (no Shapely dependency
  needed); a single `Polygon` is wrapped as a one-member `MultiPolygon` to
  satisfy the column's `NOT NULL geometry(MultiPolygon, 4326)` type.
  `boundary_type` is a pure passthrough string covering all five DDL
  values (`MUNICIPALITY`/`REGION`/`REGIONAL_UNIT`/`POSTAL_CODE`/
  `ENVIRONMENTAL_ZONE`) — "environmental layers" needed zero new code,
  just confirmed via a dedicated test
  (`test_ckan_environmental_zone_db.py`) rather than left as an unverified
  claim.
  **Schools/hospitals** (`facilities.py`, new `facilities` table —
  `db/migrations/12_facilities.sql`) write rows with an optional PostGIS
  `Point` geometry (nullable — unlike boundaries, a facility with no
  coordinates is still useful data) and a generic
  `capacity_metric`/`capacity_value` pair (`STUDENTS`/`BEDS`) behind
  §22.3's per-student/per-bed metrics. A plain CSV with optional `lat`/`lon`
  columns is assumed, not GeoJSON — simpler for point data.
  GeoJSON/CSV formats are assumed for every adapter (data.gov.gr's own
  bulk-download formats are CSV/JSON/XML/XLSX; GeoJSON is standard JSON)
  — confirm against the live dataset before relying on this.
  Every adapter is a whole-dataset snapshot (one file = the entire current
  state for that dataset scope), so a changed file (different content
  hash) replaces every row for that scope wholesale rather than upserting
  row by row — an unchanged file is a pure dedup no-op like every other
  connector. Standalone CLI (`cli.py sync-population`/`sync-metric`/
  `sync-boundaries`/`sync-facilities`, like TED's — nothing on the ΚΗΜΔΗΣ
  side triggers a catalog sync). Still unbuilt: the separate Κτηματολόγιο
  INSPIRE Geoportal API (a different API entirely, not this generic CKAN
  client); `nuts_areas` reference data isn't loaded by anything yet either
  (every adapter's `nuts_code` columns stay NULL unless the source
  dataset's own properties happen to carry one).
- Tests: `tests/unit/*` (116 tests), `tests/contract/*` (49 tests) — 165
  passing total, no DB/network needed. Beyond what's already described per
  connector above, this refinement pass added: `test_diavgeia_search_scoring.py`
  (pure candidate-scoring logic), `test_gemi_lexicon.py` (accent/case
  normalization incl. the NFD-decomposition fix), `test_ckan_facilities.py`
  (capacity/coordinate parsing, rows-without-coordinates still kept),
  `test_ckan_geo.py`/`test_ckan_boundaries.py` (already existed from the
  earlier CKAN pass), a new `_EVENT_TYPES_BY_ACT_TYPE` coverage test in
  `test_alerts_evaluate.py`, plus new contract coverage for
  `search_decisions_advanced`, `find_projects_by_beneficiary_afm`, and the
  TED Search API v3 request contract.
  `tests/integration/*` (21 files, 33 skipped total, confirmed to skip
  cleanly without `DATABASE_URL`) — beyond the set already described per
  connector above, this pass added: `test_diavgeia_signers_db.py` (signer
  → `PERSON` entity dedup-by-name across two different decisions),
  `test_ckan_metric_db.py` (a regional indicator and population coexisting
  for the same dataset without dedup-namespace collision),
  `test_ckan_environmental_zone_db.py` (proves `ENVIRONMENTAL_ZONE`
  end-to-end rather than just asserting it via CLI `--choices`, and that it
  doesn't collide with a `MUNICIPALITY` ingestion for the same dataset),
  `test_ckan_facilities_db.py` (real `ST_Point` geometry for a facility
  with coordinates, a real row with `geom IS NULL` for one without,
  wholesale replace on a changed file). The Διαύγεια search-resolve test
  file also gained one more case:
  `test_advanced_search_disambiguates_when_protocol_number_given` — two
  ambiguous SEARCH candidates correctly resolve to one via a follow-up
  ADVANCED_SEARCH call.
  Alerts got four new integration test files:
  `test_alerts_opportunity_payment_db.py` (REQUEST->opportunity.created
  then opportunity.updated on a material change; PAYMENT->payment.detected;
  AWARD fires nothing, confirming no accidental over-mapping),
  `test_alerts_contract_expiring_db.py` (only the soon-to-expire of three
  seeded contracts fires; the identical scan re-run same-day dedupes; the
  *next* day's scan is a distinct, non-deduped event),
  `test_alerts_company_status_changed_db.py` (a brand-new company's first
  snapshot does not fire — no prior status to compare against — a real
  transition fires exactly once, an unrelated-field change with the status
  unchanged does not fire), `test_alerts_buyer_new_procurement_db.py` (a
  brand-new process fires once; a second act for the same buyer that
  extends that *same* process via the chain link does not fire again;
  omitting `delivery_channel` — every pre-existing caller's behavior —
  fires nothing at all, confirming the opt-in is truly backward-compatible).
  Existing `test_gemi_resolve_db.py` assertions were also corrected here —
  they still asserted raw Greek status labels (`"ΕΝΕΡΓΗ"`) from *before*
  the GEMI lexicon work normalized `company_status` to canonical codes
  (`"ACTIVE"`); this was a real staleness bug in a DB-gated test I couldn't
  run at the time it was introduced, caught now while touching the same
  return type for the `company.status_changed` wiring.

## Remaining (this pass — depth-first on ΚΗΜΔΗΣ, then API + alerts)

- [x] **Phase A** — ΚΗΜΔΗΣ `request`/`notice`/`auction`/`payment` resources,
  generalizing the `contract`-only client/normalize/db_writer/pipeline.
- [x] **Phase B** — `adamChain` fetch + process grouping (`procurement_processes`,
  `process_members`, `process_merge_log`; controlled merge when a chain
  touches two existing processes).
- [x] **Phase C** — moved `find_or_create_entity_by_afm` into
  `services/entity_resolution/resolve.py` (source-agnostic signature: plain
  ΑΦΜ fields, not a connector-specific record type). `db_writer.py` now
  imports it, with a thin backward-compat wrapper for its own call sites.
- [x] **Phase D** — `apps/api` (FastAPI): `/v1/contracts/{adam}`,
  `/v1/processes/{id}(+/timeline)`, `/v1/search`, `/v1/buyers/{id}(+/suppliers)`,
  `/v1/companies/{id}(+/contracts)`. 9 routes total incl. `/health`.
  `packages/schemas/responses.py` has the response models. Verified: OpenAPI
  schema builds and `/health` works with no `DATABASE_URL` set (engine is
  created lazily); `tests/integration/test_api_endpoints.py` (DATABASE_URL-
  gated) seeds one contract through the real pipeline and exercises all 8
  data endpoints incl. a 404 case.
- [x] **Phase E** — `services/alerts/evaluate.py`: material-change detection
  on act upsert, `alert_rules` matching, deduplicated `alert_events` writes,
  a `DeliveryChannel` protocol with a log-only implementation.

**All five phases of the original pass are complete**, plus six follow-on
connectors and a refinement pass that closed most of their initial
depth gaps:
- [x] **Διαύγεια connector** — direct ΑΔΑ fetch + SEARCH fallback (§17.4
  `DIAVGEIA_SEARCH_MATCH`), decision storage, linkage to the originating
  ΚΗΜΔΗΣ act and its process, opt-in CLI wiring for both paths.
- [x] **ΓΕΜΗ connector** — find-by-ΑΦΜ enrichment behind the
  `CompanyRegistryProvider` protocol, temporal snapshots, cache/refresh
  policy, opt-in CLI wiring, plus attribute `search()` (not yet wired into
  any pipeline caller).
- [x] **ΑΝΑΠΤΥΞΗ connector** — all 3 programming periods, full join
  hierarchy Levels 1-4, opt-in CLI wiring with `--anaptyxi-period`.
- [x] **Composed-hook regression test** — `cli.py`'s full hook composition
  exercised directly, not just the individual resolvers.
- [x] **TED + VIES connectors** — standalone TED backfill (Search API +
  bulk XML) with buyer+CPV+date process matching (Level 3) falling back to
  buyer+title+amount+date (Level 4), VIES foreign-supplier validation
  triggered from TED.
- [x] **ΜΕΦ connector** — find-by-recipient-ΑΦΜ expense lookup, 4-tier
  confidence linkage per §20.2 (ADA+AFM/ADA+buyer/AFM+amount+date/AFM-only-
  candidate), opt-in CLI wiring, triggered by the same contractor-entity
  hook as ΓΕΜΗ.
- [x] **CKAN / data.gov.gr connector** — generic Action API client
  (package_search/package_show/resource_search), `external_datasets`
  registry, all six §22.2 dataset types (population, regional indicators,
  administrative boundaries incl. environmental zones, schools, hospitals
  — the boundaries/facilities adapters are the first real PostGIS/
  GeoAlchemy2 geometry writes in this codebase), standalone CLI.
- [x] **`/v1/search` pagination fix** — true keyset pagination, replacing
  an OFFSET-based approach that had a real duplicate/gap bug when exact-
  identifier and title-match result sets interacted across a page boundary.

**Second refinement pass** (closing most of the gaps the follow-ons above
left open — see the per-connector "Done" entries for full detail):
- [x] Διαύγεια `ADVANCED_SEARCH` (disambiguation narrower) + signers → `PERSON` entities.
- [x] ΓΕΜΗ legal-form/status lexicon (`lexicon.py` + `db/seeds/gemi_lexicons.sql`).
- [x] CKAN regional economic indicators (generalized population adapter),
  environmental zones (confirmed via a real test, no new code needed), and
  a new schools/hospitals `facilities` adapter.
- [x] **`services/alerts` event-type coverage** — 8 of 9 §30.5 event types
  now covered (`opportunity.*`, `payment.detected`, `contract.expiring`,
  `company.status_changed`, `buyer.new_procurement`, alongside the
  existing `contract.*`); `alert.triggered` deliberately not implemented
  (see the "Done" entry above for why). This also surfaced and fixed a
  real, separate gap: ΚΗΜΔΗΣ never populated `procurement_acts.end_date`
  at all — now mapped from a best-effort-guessed field, needed for
  `contract.expiring` to have anything to scan.

Two items from the original gap list were explicitly **not** attempted,
with reasons (confirmed with the user before starting this pass — see
conversation, not re-litigated here): **ΑΝΑΠΤΥΞΗ Levels 2-4 sharing one
ΑΦΜ-scoped search** is an inherent limitation of not having a second real
ΑΝΑΠΤΥΞΗ API endpoint documented, not a shortcut — nothing to build without
fabricating a fictional contract. **TED Levels 1-2** need the documents
pipeline (PDF/OCR) to find a TED reference embedded in a contract
document — a genuinely separate, large subsystem, explicitly deferred.

**Nothing left open from this refinement pass** — every item either done
or explicitly deferred with a reason, above.

See "Not yet implemented" notes in each touched module's README for
smaller remaining gaps (Διαύγεια `ORGANIZATION_LOOKUP`/`SIGNER_LOOKUP`,
ΓΕΜΗ `search()` still has no pipeline caller, CKAN's separate INSPIRE
Geoportal API, `nuts_areas` reference data still unloaded).

## Explicitly out of scope for this pass

**Every item previously listed here is now done** — scheduled analytics-
mart refresh, real ingestion scheduling/orchestration, real alert delivery
channels (email/webhook/Teams/Slack), auth/RLS enforcement, OpenSearch
full-text search, and `apps/web` — see the six "Done" entries above (most
recent first). Caveats worth remembering, since "done" here means "real
and tested," not "matches the recommended stack verbatim":

- Real ingestion *scheduling* is real (ΚΗΜΔΗΣ + TED now) but real
  *orchestration* in the fuller sense (Prefect/Dagster/Celery, per §11's
  recommended stack) was deliberately not built — a Postgres-backed
  scheduler was judged sufficient for MVP scope, same call already made
  for `/v1/search` and the analytics marts themselves. CKAN still isn't
  scheduled — a different, whole-dataset-refresh job shape, not a gap in
  the current mechanism.
- Auth: only `/v1/alert-rules` is gated — procurement data stays
  intentionally open (§38's "shared public data"), and no IdP is deployed
  (`packages/auth/` works with any OIDC-compliant one, none chosen).
- OpenSearch: `/v1/search/fulltext` is a new, separate endpoint — the
  original `/v1/search` still does its own Postgres-based
  exact-match-first ranking, untouched.
- `apps/web`: one reference demo path (search → process → buyer/company),
  not the full §31 screen list; no login UI, no map, no component library.

Nothing is queued next unless asked — every subsystem named in the
original "explicitly out of scope" list has at least a real, tested first
version now.

The documents pipeline (§23/§24) is no longer in this list either — see the "Done"
entry above (`services/documents/`). What's still missing there: no
connector automatically *calls* `process_document()` yet for a discovered
document URL (Διαύγεια's `document_url` field, ΚΗΜΔΗΣ tender attachments —
it's a standalone CLI invocation for now, same posture as TED's/CKAN's
CLIs); a real `ClamdAntivirusScanner` now exists (`services/documents/clamav.py`)
but stays opt-in — `process_document()` still defaults to the no-op
scanner, and no `clamd` daemon is reachable in this sandbox to run the
real one against; and an LLM integration for §23.5's summarization/QA/
classification-suggestion uses (deliberately not started — no provider/
cost decision has been made, and none of the extraction targets need an
LLM to be useful).

## Blockers / needs confirmation against the live APIs

- ΑΔΑ/ΑΔΑΜ regex shape in `services/documents/entities.py` — inferred from
  real-world examples, not a confirmed spec format (description.txt §7.2
  gives normalization rules, not a character-count/regex). Widen/narrow the
  `{6,10}`/`{3,4}` ranges if a real document turns up a shape outside them.
  **Genuinely blocked** — needs a real document sample, no code action
  possible without one.
- No `clamd` daemon is reachable in this sandbox to run
  `services/documents/clamav.py::ClamdAntivirusScanner` against for real
  (confirmed: not installed, no passwordless `sudo` to install one) — the
  real protocol client exists and is tested (a fake in-process server
  proves the wire protocol; a `CLAMD_HOST`-gated integration test is ready
  for whoever has a real daemon), but `process_document()` still defaults
  to `NoOpAntivirusScanner` unless a caller passes the real one in.
- `KHMDHS_API_BASE_URL` — not set anywhere; must be confirmed before hitting
  anything but mocked tests (`services/ingestion/connectors/khmdhs/config.py`).
- ΚΗΜΔΗΣ request-body field names for the date window, and response-envelope
  field names (`data`/`isLastPage`) — best-effort guesses, flagged with
  `# TODO` in `client.py`.
- ΚΗΜΔΗΣ contract `end_date` (duration end) field name —
  `contractEndDate`/`endDate`/`contractDurationEndDate` are guesses in
  `normalize.py`; §16's field list names no such field, only §27.11's
  renewal-window logic assumes one exists. Until confirmed,
  `services/alerts/evaluate.py::evaluate_expiring_contracts_and_fire()`
  (§30.5's `contract.expiring`) is real, tested logic with nothing to
  scan in practice.
- `adamChain` response shape — implemented against a best-effort guess
  (`adamchain.py::_extract_chain_adams` tolerates a few plausible envelope
  shapes); also unconfirmed which `link_type` should connect two acts in a
  chain, so every pair is linked `RELATED_TO` for now (safe, but coarser
  than the ideal §15.7 vocabulary — tighten once the real shape is known).
- `DIAVGEIA_API_BASE_URL` — not set anywhere; needed only for
  `--with-diavgeia`. Even less confirmed than ΚΗΜΔΗΣ: description.txt gives
  no endpoint paths at all for Διαύγεια, so `GET /decisions/{ada}` in
  `connectors/diavgeia/client.py` and every field name in `normalize.py`
  are guesses pending a real sample payload.
- `GEMI_API_BASE_URL`/`GEMI_API_KEY` — not set anywhere; needed only for
  `--with-gemi`. Same "no endpoint paths given" situation as Διαύγεια:
  `GET /companies?vatNumber=` + `X-API-Key` header in
  `connectors/gemi/client.py`, and every field name in `normalize.py`, are
  guesses. The ΓΕΜΗ status vocabulary used by `cache.is_stable_status()` is
  also unconfirmed — description.txt doesn't enumerate it.
- `ANAPTYXI_API_BASE_URL` — not set anywhere; needed only for
  `--with-anaptyxi`. `GET /projects?misCode=` in `connectors/anaptyxi/client.py`
  and every field name in `normalize.py` are guesses — description.txt
  describes the 2014-2020 API's resource types conceptually but not its
  request/response shape.
- `TED_API_BASE_URL` — not set anywhere; required to run
  `connectors/ted/cli.py` at all. `POST /search` and the response envelope
  field names (`notices`/`isLastPage`) in `client.py`, and every field name
  in `normalize.py`, are guesses — TED is the one source description.txt
  confirms needs no auth, but that's not the same as a confirmed hostname
  or request/response contract.
- `VIES_API_BASE_URL` — not set anywhere; needed only for `--with-vies`.
  The `checkVat` SOAP envelope and response-parsing regex in
  `connectors/vies/client.py` are built against the general shape of the
  publicly documented VIES WSDL, not a captured real response — confirm
  before relying on it.
- `MEF_API_BASE_URL` — not set anywhere; needed only for `--with-mef`.
  `GET /expenses?recipientAfm=` and the response envelope field name
  (`expenses`/`data`) in `connectors/mef/client.py`, and every field name
  in `normalize.py`, are guesses — description.txt describes ΜΕΦ's role
  and join-hierarchy conceptually (§3.5, §20) but gives no endpoint paths
  or sample payload.
- `CKAN_API_BASE_URL` — not set anywhere; required to run
  `connectors/ckan/cli.py`. `package_search`/`package_show`/
  `resource_search` are standard CKAN Action API operation names (unlike
  every other source's paths, these are the generic CKAN software's own
  documented contract, not data.gov.gr-specific), but description.txt
  itself says the exact paths/limits must still be confirmed against the
  live deployment — treat `client.py`'s `/api/3/action/<name>` paths as a
  well-known-but-unconfirmed baseline, same posture as VIES's public WSDL.
  The population-CSV column names in `connectors/ckan/normalize.py` are a
  full guess — no sample population-by-municipality file was available.
  Same for the administrative-boundaries GeoJSON property names in
  `connectors/ckan/boundaries.py` — additionally, **GeoJSON itself is an
  assumption** about the resource format (data.gov.gr's own announcement
  lists CSV/JSON/XML/XLSX bulk downloads, not GeoJSON explicitly); if the
  live dataset turns out to be Shapefile or KML instead, `geo.py`'s
  GeoJSON-to-WKT converter needs a different input parser (would likely
  need a real geometry library at that point, unlike GeoJSON's plain-JSON
  coordinate arrays). Same again for the schools/hospitals CSV column
  names in `connectors/ckan/facilities.py` (`lat`/`lon`/`capacity` etc.) —
  a plain-CSV-with-coordinate-columns format is assumed rather than
  GeoJSON, on the theory that point facility data is more likely to ship
  as tabular data than a geometry format; unconfirmed either way.
- ΓΕΜΗ/Διαύγεια vocabulary in `gemi/lexicon.py` — the legal-form/
  company-status *concepts* (ΑΕ/ΙΚΕ/.../ACTIVE/SUSPENDED/...) are real,
  publicly documented Greek company-law terminology, not a guess about
  either API specifically; what's unconfirmed is which exact raw string
  spelling each API actually sends for each — the lexicon maps several
  plausible variants per code, but a real payload could reveal a spelling
  not covered (falls back to a normalized-but-unrecognized passthrough,
  not a crash or a dropped value, per `lexicon.py`'s own doc).

## Environment note

No Docker/Postgres/network access confirmed working in the sandbox this was
built in — `pytest tests/unit tests/contract` is what was actually run and
verified; `tests/integration/*` and any live-API calls need to be run by
whoever has that access, per `docs/runbooks/local-dev.md`.

An existing local checkout needs two things re-applied after this pass:
`pip install -e ".[dev]"` again (picks up `geoalchemy2`/`pypdfium2`/
`pillow`/`pyjwt[crypto]`, added to `pyproject.toml`'s runtime
`dependencies` across this and the two preceding passes), and on an
**already-migrated** database, apply the new migrations directly, in order —
`psql $DATABASE_URL -f db/migrations/10_funding_links_review.sql` (adds
`funding_links.reviewed_by`), `11_gemi_lexicons.sql` (new reference
tables), `12_facilities.sql` (new `facilities` table), `13_document_pages.sql`
(per-page document text + tsvector), `14_mart_refresh_state.sql` (mart
refresh watermarks), `15_alert_delivery_targets.sql` (concrete per-rule
delivery destinations), `16_row_level_security.sql` (creates the
`procintel_app` role + enables RLS on tenant-scoped tables — rotate its
placeholder password, see `docs/runbooks/local-dev.md` §11), then
`psql $DATABASE_URL -f db/seeds/gemi_lexicons.sql` to populate the ΓΕΜΗ
vocabulary — rather than
re-running `./db/run_migrations.sh` wholesale, which would fail
re-creating tables 01-09 already made. A fresh database still just runs
`./db/run_migrations.sh` once, as before; it now picks up `10_*.sql`
through `15_*.sql` and `db/seeds/*.sql` too (the runner applies
migrations, then marts, then seeds, each glob in filename order — seeds
are a new final step, previously nothing loaded them at all).
