# connectors/diavgeia

Διαύγεια connector (spec §3.2, §17).

## Status: implemented (direct ΑΔΑ fetch + SEARCH + ADVANCED_SEARCH + signers)

| Module | Purpose |
|---|---|
| `config.py` | `DiavgeiaConnectorConfig` — `DIAVGEIA_API_BASE_URL` env var, no default (unconfirmed hostname/paths, see `docs/source-contracts/diavgeia.md`) |
| `client.py` | `DiavgeiaClient.fetch_decision_by_ada()`/`search_decisions()`/`search_decisions_advanced()` — rate limiting, retry/backoff, **separate circuit breakers per capability** (a degraded SEARCH/ADVANCED_SEARCH must never block direct fetch, §17.3), and a `capabilities` dict tracking `DIRECT_ADA_FETCH`/`SEARCH`/`ADVANCED_SEARCH`/`ORGANIZATION_LOOKUP`/`SIGNER_LOOKUP`/`VERSION_LOG` status |
| `normalize.py` | Raw decision → `NormalizedDecision`; `normalize_ada()` (§7.2); `signer_names` extraction (best-effort field-name guess) |
| `db_writer.py` | Idempotent upsert of a `DIAVGEIA_DECISION` act, keyed on the ΑΔΑ identifier. Signer names become (or are matched to) `PERSON` entities linked via `act_parties(party_role='SIGNER_PERSON')` — replaced wholesale on every upsert, §6.3's explicit name-only-entity exception for signers |
| `resolve.py` | `resolve_decision_for_ada()` — direct fetch + store + `act_links(APPROVES, EXACT_ADA, confidence=1.0)`. `resolve_decision_via_search()` — §17.4's fallback tier: organization + title match (both required, via `services/entity_resolution/text_similarity.py`) on a single unambiguous candidate, `act_links(APPROVES, DIAVGEIA_SEARCH_MATCH, confidence=0.75)`; when SEARCH alone yields zero/multiple candidates and a `decision_type`/`protocol_number` is available, one ADVANCED_SEARCH retry narrows before giving up. Both accept `process_documents=True` (opt-in) to also run the decision's own PDF (`document_url`) through `services/documents/pipeline.py::process_document()` — a document failure is logged, never raised, since the decision itself is already linked/stored by that point |

Wired into the ΚΗΜΔΗΣ CLI (`connectors/khmdhs/cli.py --with-diavgeia`,
**opt-in**, off by default — unlike `--no-adam-chain`/`--no-alerts`, this
needs a second unconfirmed base URL configured, so it doesn't turn on
silently). Direct fetch is triggered per `ActUpsertResult.related_ada` —
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
`ORGANIZATION_LOOKUP`/`SIGNER_LOOKUP`/`VERSION_LOG` remain tracked as
`UNKNOWN` (not implemented, not probed).

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

## Not yet implemented

Issuing-authority entity resolution (no reliable identifier confirmed yet
— kept as plain text, deliberately, unlike signers), version-log-aware re-fetching,
`ORGANIZATION_LOOKUP`/`SIGNER_LOOKUP` (retrieving signer/org *reference
data* directly from Διαύγεια, distinct from extracting signer names
already present on a fetched decision, which `normalize.py`/`db_writer.py`
now do).
