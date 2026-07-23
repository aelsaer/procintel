# connectors/ted

TED Search API connector (spec §3.8, §21).

## Status: implemented (search + ingest, join hierarchy Levels 3-4)

| Module | Purpose |
|---|---|
| `config.py` | `TedConnectorConfig` — official `https://api.ted.europa.eu` default, optional override, no authentication |
| `client.py` | `POST /v3/notices/search` with expert query, official field names, 250-row pages, total-count pagination, rate limiting, retry/backoff, and circuit breaker |
| `normalize.py` | Raw notice → `NormalizedTedNotice`. `_detect_eforms_version()` implements §21.2's version-awareness requirement (explicit marker → confidence 1.0; legacy-form marker → confidence 1.0, no version; ambiguous shape → 0.6; unrecognized shape → 0.3) without pretending to distinguish eForms A from eForms B without real samples. `parse_bulk_xml_package()` remains a tested parser for operator-supplied XML packages; no unverified transport URL is exposed |
| `db_writer.py` | Idempotent upsert of a `TED_NOTICE` act + `ted_notice_details`. Buyer/supplier resolution: Greek parties (`country_code == 'GR'`) go through the same shared `services/entity_resolution` ΑΦΜ resolver everything else uses; non-Greek parties get their own `entity_identifiers(scheme='EU_VAT')` lookup-or-create, since the Greek ΑΦΜ checksum doesn't apply and validity is VIES's job, not this module's |
| `resolve.py` | `resolve_notice_process_link()` — join hierarchy **Levels 3-4**: Level 3 (buyer ΑΦΜ + CPV + ±180-day publication-date window, confidence 0.85) tried first; if it finds zero or multiple candidates, Level 4 (same buyer, no CPV requirement — title similarity + ±15% amount tolerance instead, confidence 0.65) is tried as a fallback. Both link only when the query returns exactly one distinct candidate process — zero or multiple means "not confident enough," left unlinked |
| `pipeline.py` | `ingest_ted_partition()` standalone Search API backfill loop; TED is not triggered from ΚΗΜΔΗΣ because no source identifier is known up front |
| `cli.py` | `backfill --date-from ... --date-to ... [--country GR] [--with-vies]`; process matching always runs and VIES foreign-supplier validation is optional |
| `scheduled.py` | `run_scheduled_window()` — the second job wired into `services/ingestion/orchestration/jobs.py::default_jobs()` (alongside ΚΗΜΔΗΣ), same date-windowed `ScheduledJob` shape. Always-on process matching, deliberately not VIES (needs its own confirmed base URL) |

## Join hierarchy (§21.3) — Levels 3-4 implemented

1. Exact TED notice ID — not implemented; nothing in ΚΗΜΔΗΣ's preserved
   field list (§16) carries a TED reference.
2. Explicit document reference — not implemented; needs the documents
   pipeline (still out of scope).
3. **Buyer VAT + publication date + CPV** — implemented (`resolve.py`),
   confidence 0.85.
4. **Buyer name + title + amount + date** (no exact identifier) —
   implemented as a fallback when Level 3 finds nothing, via
   `services/entity_resolution/text_similarity.py`; confidence 0.65.
5. Manual review — no dedicated queue/UI; Level 4 links are left with
   `act_links.reviewed_by IS NULL`, the same signal `act_links` already
   supported and `connectors/anaptyxi` now uses too (`act_links WHERE
   confidence < 0.85 AND reviewed_by IS NULL` doubles as the review
   queue).

Levels 1-2 remain unreachable without the documents pipeline: ΚΗΜΔΗΣ's own
field list carries no TED reference, so there's nothing to trigger an
exact-ID or document-reference match from.

## Not yet implemented

A genuine eForms-A-vs-B distinction (needs real samples),
`related_notice_ids`-based linking between TED notices themselves,
European cross-country comparison views (analytics, not ingestion). Bulk
packages are not exposed by the CLI because daily packages are keyed by
OJ S issue number, not a calendar date; date-window ingestion uses Search
API v3 instead of constructing a false bulk URL.
