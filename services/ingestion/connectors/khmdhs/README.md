# connectors/khmdhs

ΚΗΜΔΗΣ Open Data connector — the system's spine (spec §3.1, §16). All five
paginated resources (`request`, `notice`, `auction`, `contract`, `payment`)
plus `adamChain` lifecycle resolution and process grouping (Βήμα 3-4) are
implemented.

## Status: implemented (all five resources + adamChain/process grouping)

| Module | Purpose |
|---|---|
| `afm.py` | Greek ΑΦΜ checksum validator (§7.2) |
| `config.py` | `KhmdhsConnectorConfig` — `KHMDHS_API_BASE_URL` env var, no default (unconfirmed hostname, see `docs/source-contracts/khmdhs.md`) |
| `client.py` | `KhmdhsClient.fetch_resource_page(resource, ...)` + `fetch_adam_chain(reference_number)` — rate limiting, retry/backoff, circuit breaker, shared across all five resources (`ALL_RESOURCES`) |
| `normalize.py` | `normalize_khmdhs_record(raw, resource=...)` → `NormalizedAct`, handling `contractRelatedAda`/`contractRelatedADA` casing drift, resource→act_type mapping (request→REQUEST, notice→NOTICE, auction→AWARD, contract→CONTRACT, payment→PAYMENT), and the two separate funding-reference fields (§19.4) |
| `db_writer.py` | `upsert_act()` / `ingest_khmdhs_record()` — idempotent canonical upsert (`source_records`, `entities`, `entity_identifiers`, `procurement_acts`, `act_identifiers`, `act_cpv_codes`, `act_locations`, `act_parties`), parameterized by act_type. Returns `ActUpsertResult`/`IngestResult` (insert-vs-changed-fields, `related_ada`, `contractor_entity_id`/`contractor_afm_normalized`, `funding_ref_candidates` — see below) |
| `adamchain.py` | `resolve_adam_chain_for_act()` — fetches `adamChain` for a ΑΔΑΜ, links related acts (`act_links`, `link_method='ADAMCHAIN'`), and assigns/merges the `procurement_processes` row via `process_members`/`process_merge_log` (§16.5-16.6). Keeps `procurement_acts.process_id` (the denormalized pointer `db/marts/procurement_360.sql` actually reads) in sync with `process_members` on every assignment and merge repoint — a real bug, found and fixed, see `PROGRESS.md` |
| `pipeline.py` | `ingest_khmdhs_partition(resource=..., on_ingest_result=...)` — the fetch→raw→normalize→upsert loop (§16.4), with one optional post-ingest hook `(conn, resource, IngestResult) -> None` that `adamchain.py`, `services/alerts`, and the `diavgeia`/`gemi`/`anaptyxi` connectors all plug into (composed in `cli.py`, not this module) |
| `cli.py` | `python -m services.ingestion.connectors.khmdhs.cli backfill --date-from ... --date-to ... [--resource ...] [--no-adam-chain] [--no-alerts] [--with-diavgeia] [--with-gemi] [--with-anaptyxi]` (repeatable `--resource`, defaults to all five; adamChain + alerts on by default, Διαύγεια/ΓΕΜΗ/ΑΝΑΠΤΥΞΗ resolution **opt-in**). `tests/integration/test_khmdhs_cli_composed_db.py` exercises this composition directly, not just the individual resolvers |

A `PAYMENT`/`AWARD`/etc. act with no linked `CONTRACT` act yet — or two acts
that adamChain hasn't connected yet — is expected while a backfill is still
in progress (§26.1 `PARTIAL_LIFECYCLE`), not an error. Process merges are
controlled and audited: the merged-away `procurement_processes` row is kept
(`record_status='MERGED'`, `merged_into_process_id` set) so its `public_id`
stays resolvable, never deleted.

`normalized.related_ada` (decisionRelatedAda/contractRelatedAda/
cancellationADA) is **not** written as an `act_identifiers` row on the
ΚΗΜΔΗΣ act — those ΑΔΑ values name a *different* act (a Διαύγεια decision),
and `act_identifiers` has a global unique index on (scheme, value). It's
exposed on `ActUpsertResult.related_ada` purely as a trigger list for
`services/ingestion/connectors/diavgeia` (§17.1). See `db_writer.py`'s
module docstring for the full story — this was a real modeling bug caught
and fixed while wiring Διαύγεια in.

`ActUpsertResult.funding_ref_candidates` (`publicFundingRefOps` +
`espaFundProgramRef`, §19.4) is the same pattern deliberately applied from
the start this time: a pure trigger list for
`services/ingestion/connectors/anaptyxi`, never assumed to be a ΟΠΣ/MIS code
by this module itself.

Several details are best-effort guesses pending confirmation against the
live API (each flagged with a `TODO`/module docstring note): the
request-body field names for the date window, the response envelope's field
names, and — for `request`/`notice`/`payment` specifically — a couple of
extra fallback field names for dates/amounts where the spec's field list
doesn't spell out resource-specific naming (`normalize.py`'s
`_EXTRA_*_KEYS` maps). Tests (`tests/unit`, `tests/contract`) don't depend
on any of these being correct — they run against local, clearly-labeled
synthetic fixtures (`tests/fixtures/khmdhs/*_sample.json`), not the live
API.

See `docs/runbooks/local-dev.md` for how to run this end-to-end, and
`docs/source-contracts/khmdhs.md` for the source contract this implements.
