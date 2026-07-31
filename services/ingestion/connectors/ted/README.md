# connectors/ted

TED Search API connector (spec §3.8, §21).

## Status: implemented

| Module | Purpose |
|---|---|
| `config.py` | `TedConnectorConfig` — official `https://api.ted.europa.eu` default, optional override, no authentication |
| `client.py` | `POST /v3/notices/search` with expert query, official field names, 250-row pages, total-count pagination, rate limiting, retry/backoff, and circuit breaker |
| `normalize.py` | Version-aware Search API/eForms normalization, SDK customization/version detection, previous-notice references and tested XML bulk parsing; unknown shapes are retained with reduced parse confidence |
| `db_writer.py` | Idempotent versioned TED acts/details, exact procedure-reference links, related-notice/version chains, and Greek/EU supplier identity handling |
| `resolve.py` | `resolve_notice_process_link()` — join hierarchy **Levels 3-4**: Level 3 (buyer ΑΦΜ + CPV + ±180-day publication-date window, confidence 0.85) tried first; if it finds zero or multiple candidates, Level 4 (same buyer, no CPV requirement — title similarity + ±15% amount tolerance instead, confidence 0.65) is tried as a fallback. Both link only when the query returns exactly one distinct candidate process — zero or multiple means "not confident enough," left unlinked |
| `pipeline.py` | `ingest_ted_partition()` standalone Search API backfill loop; TED is not triggered from ΚΗΜΔΗΣ because no source identifier is known up front |
| `cli.py` | `backfill --date-from ... --date-to ... [--country GR] [--with-vies]`; process matching always runs and VIES foreign-supplier validation is optional |
| `scheduled.py` | `run_scheduled_window()` — the second daily job alongside ΚΗΜΔΗΣ. It runs process matching, bounded VIES validation for foreign suppliers, and incremental OpenSearch indexing when configured |

## Join hierarchy (§21.3)

1. **Exact TED notice/procedure identifier** — links source identifiers
   already present in canonical acts with `TED_EXACT_PROCEDURE_ID`.
2. **Explicit related-notice reference** — links previous, change and
   superseding notices with `TED_EXACT_REFERENCE`/`TED_NOTICE_VERSION`.
3. **Buyer VAT + publication date + CPV** — implemented (`resolve.py`),
   confidence 0.85.
4. **Buyer name + title + amount + date** (no exact identifier) —
   implemented as a fallback when Level 3 finds nothing, via
   `services/entity_resolution/text_similarity.py`; confidence 0.65.
5. **Evidence-bearing fuzzy link** — Level 4 retains confidence, method and
   clickable evidence in the Relationship Explorer and is never presented
   as an exact cross-reference.

Every notice version retains parser version, detected eForms/SDK version,
notice version and parse confidence. Known versions become the latest
canonical version; ambiguous shapes remain queryable evidence rather than
being silently coerced or dropped. Date-window ingestion uses Search API
v3; operator-supplied OJ S bulk packages use the XML parser.
