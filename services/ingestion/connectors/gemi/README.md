# connectors/gemi

Open Data ΓΕΜΗ connector (spec §3.3, §18). Company identity enrichment keyed
by ΑΦΜ.

## Status: implemented (find-by-ΑΦΜ enrichment)

| Module | Purpose |
|---|---|
| `config.py` | `GemiConnectorConfig` — published Open Data v1 base URL by default; `GEMI_API_KEY` is required |
| `client.py` | Published `/companies`, `/companies/{arGemi}`, and `/companies/{arGemi}/documents` resources with the official `api_key` header, rate limiting, retry/backoff, and circuit breaker |
| `provider.py` | `CompanyRegistryProvider` Protocol (description.txt §18.4's exact interface) + `GemiCompanyRegistryProvider` implementation, incl. `search(CompanySearchQuery)` (name/kad/status/prefecture/municipality, per §18.4's parameter list) returning a plain list — no results is a legitimate empty list, not an error. Enrichment logic is built against the Protocol, not `GemiClient` directly, so the MVP doesn't functionally depend on API key approval — a different provider can stand in later |
| `normalize.py` | Raw record → `NormalizedCompany`. `legal_form_code`/`company_status` are normalized against `lexicon.py`'s canonical vocabulary, not passed through raw — `legal_form` keeps the original human label for display |
| `lexicon.py` | `LEGAL_FORM_LEXICON`/`COMPANY_STATUS_LEXICON` — real Greek company-law/ΓΕΜΗ-status vocabulary (accent-insensitive lookup; unrecognized labels pass through normalized rather than being dropped). Mirrored as queryable reference tables in `db/seeds/gemi_lexicons.sql` (`db/migrations/11_gemi_lexicons.sql`) |
| `db_writer.py` | Temporal snapshot upsert: only writes a new `entity_company_snapshots` row when the company-relevant fields actually differ from the current one, closing out the previous row (`is_current=False`, `valid_to` set) rather than overwriting it (§18.2). Attaches `entity_identifiers(scheme='GEMI')` |
| `cache.py` | `should_refresh()` — §18.3's policy: new company → immediate lookup; stable status → 30-day refresh; anything else ("in transition") → 7-day refresh; negative result → 7-day refresh, never permanent. `is_stable_status()` now checks against `lexicon.STABLE_STATUSES` (canonical codes only) |
| `resolve.py` | `resolve_company_snapshot()` — the full §18.1 flow: cache check → lookup → store raw → snapshot |

Wired into the ΚΗΜΔΗΣ CLI (`connectors/khmdhs/cli.py --with-gemi`,
**opt-in** like `--with-diavgeia` and requiring an API key). Triggered per
`ActUpsertResult.contractor_entity_id` — every
new/refreshed contractor entity a ΚΗΜΔΗΣ act resolves.

## Cache policy in practice

The refresh gate looks at the *last check* (from `source_records`, both
successful lookups and negative-result markers), not the entity's own
`updated_at` — so "how long ago did we last ask ΓΕΜΗ about this ΑΦΜ"
survives independently of anything else touching the entity. A negative
result gets its own dated `source_records` row
(`resource_type='company_not_found'`) rather than a snapshot, so it expires
and gets rechecked rather than blocking lookups forever.

"In transition" status detection (`cache.is_stable_status()`) checks
`lexicon.STABLE_STATUSES` (just `{"ACTIVE"}`) — everything else, including
any raw label the lexicon doesn't recognize, gets the shorter refresh
window. The lexicon vocabulary itself (ΑΕ/ΕΠΕ/ΙΚΕ/... legal forms,
ACTIVE/SUSPENDED/... statuses) is real, publicly documented Greek
company-law terminology, not a guess about ΓΕΜΗ's API specifically — what's
still unconfirmed is exactly which raw string spelling ΓΕΜΗ's API sends
for each, hence the handful of variants mapped per code.

## Not yet implemented

`find_by_gemi` wiring into `resolve.py` (only ΑΦΜ-triggered lookup is used
so far — `search()` is implemented but has no caller yet either; it's a
capability for future use cases like entity-resolution disambiguation or a
manual lookup UI, not something the ΑΦΜ-triggered enrichment flow needs),
and a query UI for attribute searches. Public documents and announcements
are already retrieved with each refreshed company and preserved in the raw
snapshot.
