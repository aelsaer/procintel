# connectors/diavgeia

Διαύγεια connector (spec §3.2, §17).

## Status: implemented

| Module | Purpose |
|---|---|
| `config.py` | `DiavgeiaConnectorConfig` — official Open Data host by default; `DIAVGEIA_API_BASE_URL` is an optional override |
| `client.py` | Direct ΑΔΑ fetch, search/advanced search, version history, organization/unit and signer reference lookups, with rate limiting, retry/backoff and separate capability circuit breakers |
| `normalize.py` | Raw decision → `NormalizedDecision`; `normalize_ada()` (§7.2); `signer_names` extraction (best-effort field-name guess) |
| `db_writer.py` | Idempotent upsert of a `DIAVGEIA_DECISION` act, keyed on the ΑΔΑ identifier. Signer names become (or are matched to) `PERSON` entities linked via `act_parties(party_role='SIGNER_PERSON')` — replaced wholesale on every upsert, §6.3's explicit name-only-entity exception for signers |
| `resolve.py` | Direct and search-based resolution, exact process linking, PDF processing, version-chain ingestion and raw reference retention for organizations, units and signers |

Wired into the ΚΗΜΔΗΣ CLI (`connectors/khmdhs/cli.py --with-diavgeia`,
opt-in for manual backfills) and enabled in the daily enrichment cycle.
Direct fetch is triggered per `ActUpsertResult.related_ada` —
every `decisionRelatedAda`/`contractRelatedAda`/`cancellationADA` value a
ΚΗΜΔΗΣ act carries (§17.1). SEARCH fallback is a separate opt-in
(`--with-diavgeia-search`, requires `--with-diavgeia` too) attempted only
when no ΑΔΑ resolved directly for that act (or none was referenced at
all), using the act's own title and its buyer entity's name as the query.

## Runs in degraded mode by design (§17.3)

`DIRECT_ADA_FETCH` is the primary, always-attempted path; `SEARCH` and
`ADVANCED_SEARCH` are secondary, best-effort fallbacks each with their
**own circuit breaker** so a degraded/unavailable one never blocks the
others — the spec's explicit requirement. `ADVANCED_SEARCH` is used as a
disambiguation narrower (extra `decision_type`/`protocol_number`/
`unit_label` filters) when basic SEARCH alone can't land on one
unambiguous candidate, not as an independent linkage tier — §17.4 only
describes one confidence for "search by title or organization".
Capabilities begin as `UNKNOWN` and move to `AVAILABLE` or `DEGRADED` from
real calls. A failing reference endpoint does not block decision retrieval.

## Signers (§6.3's name-only `PERSON` entity exception)

Every other entity in this codebase requires a real identifier (ΑΦΜ, ΓΕΜΗ,
...) before it can be created — §8's matching hierarchy never allows a
name alone. Signers are the one explicit spec exception: §6.3 permits
storing natural persons "only when necessary to represent a publicly
published act, such as the signer, and with a limited set of fields".
`db_writer.py::_find_or_create_person_entity()` dedups purely on
normalized name, a known, weaker identity guarantee than everywhere else —
two different people who happen to share a name become one `PERSON`
entity. The issuing authority / organizational unit are *not* given this
treatment (still plain text) — that was a deliberate design choice already
in place, not something this change touches.

## A modeling fix worth knowing about

Earlier, the ΚΗΜΔΗΣ connector attached `related_ada` values as
`act_identifiers(scheme='ADA')` rows on the ΚΗΜΔΗΣ act itself. That was
wrong: an ΑΔΑ referenced by a ΚΗΜΔΗΣ act names a *different* act (the
Διαύγεια decision), and `act_identifiers` has a global unique index on
(scheme, value) — the moment this connector tried to create the real
decision act under the same ΑΔΑ, it would instead find and silently
overwrite the ΚΗΜΔΗΣ act's title/date with the decision's. Fixed in
`connectors/khmdhs/db_writer.py` before this connector was wired in — see
its module docstring. `tests/integration/test_diavgeia_resolve_db.py`
explicitly asserts the origin act's fields are untouched.

## Identity boundary

Reference organizations and signers are retained with their official
identifiers and source evidence. They are merged into canonical entities
only when the shared resolver has sufficient identity evidence; the
connector does not invent an ΑΦΜ from a name.
