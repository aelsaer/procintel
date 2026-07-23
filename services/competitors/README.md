# services/competitors

Confirmed-participation evidence — the persistence layer behind §16.3/§30's
distinction between a *fact* (someone actually bid or won) and an
*inference* (a market cohort guessed from shared CPV/buyer/region
activity). The inference side (`CONFIRMED_BIDDER`/`MARKET_COMPETITOR`/
`CONFIRMED_WINNER` classification, similarity scoring, head-to-head
counts) is a single large query directly in
`apps/api/routers/competitors.py` (`GET /v1/competitors/discover`,
`GET /v1/competitors/{id}`) — not in this package. This package only
answers "how does a confirmed participation fact get into
`process_participations` in the first place".

| Module | Purpose |
|---|---|
| `participation.py` | `record_participation()` — idempotent insert into `process_participations`, deduped on a SHA-256 `evidence_key` (process + role + identity + source/document/page) so the same fact from the same evidence never double-counts even if re-ingested. `record_document_participant()` — the documents-pipeline entrypoint (see below): resolves/creates the participant's `COMPANY` entity by ΑΦΜ (`services/entity_resolution/resolve.py::find_or_create_entity_by_afm`) and records an evidence_type=`DOCUMENT_EXTRACTED` fact, its confidence scaled down for OCR-derived text (`ocr_mean_confidence`). `backfill_winner_participations()` — a one-time-per-environment backfill deriving `WINNER` facts (evidence_type=`OFFICIAL_SOURCE`, confidence 1.0) from existing `act_parties` rows with `party_role IN ('SUPPLIER','CONTRACTOR')`, for acts ingested before `process_participations` existed. |

## Wiring

`services/documents/pipeline.py::process_document()` calls
`record_document_participant()` for every `ExtractedProcurementParticipant`
its entity extractor (`services/documents/entities.py`) finds in a page —
this is how a bidder named only in a PDF (never in `act_parties`) becomes
a `CONFIRMED_PARTICIPANT` fact instead of staying invisible. Run
`scripts/backfill_competition.py` once per environment (after the
`act_parties`/documents backfills it depends on) to seed
`process_participations` from data ingested before this package existed;
it is not on the orchestration scheduler, since it's a one-time catch-up,
not a recurring job.

## Not yet implemented

- No participation fact from ΤΕΔ/ΓΕΜΗ/ΜΕΦ signals yet — only official
  `act_parties` awards and document-extracted mentions feed this table.
- `discover_competitors()`'s scoring/classification logic living directly
  in the router (not here) is a known layering wart worth revisiting if
  it grows more scoring dimensions — not attempted in this pass since it
  works correctly as-is and moving it is a pure refactor, not a gap.
