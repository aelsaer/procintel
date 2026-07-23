# connectors/anaptyxi

ΑΝΑΠΤΥΞΗ.gov.gr connector (spec §3.4, §19). Separate adapters per programming
period (`ANAPTYXI_2007_2013`, `ANAPTYXI_2014_2020`, `ANAPTYXI_2021_2027`)
converging on the same canonical `funding_projects` schema.

## Status: implemented (all 3 periods, join hierarchy Levels 1-4)

| Module | Purpose |
|---|---|
| `config.py` | `AnaptyxiConnectorConfig` — one base-URL env var **per programming period** (`ANAPTYXI_2007_2013_API_BASE_URL`/`ANAPTYXI_2014_2020_API_BASE_URL`/`ANAPTYXI_2021_2027_API_BASE_URL`, §19.3 — each is very likely a separate deployment, not a query parameter on one system), no defaults; `ANAPTYXI_API_BASE_URL` (unsuffixed) is a backward-compatible alias for 2014-2020 |
| `client.py` | `AnaptyxiClient.find_project_by_mis()` (Level 1) / `find_projects_by_beneficiary_afm()` (Levels 2-4's shared candidate search) — rate limiting, retry/backoff, circuit breaker |
| `normalize.py` | Raw record → `NormalizedFundingProject` |
| `db_writer.py` | Idempotent upsert into `funding_projects` (application-level find-by-`(mis_ops_code, program_period)`, since there's no DB-level unique constraint on it — looser than ΚΗΜΔΗΣ's ΑΔΑΜ or Διαύγεια's ΑΔΑ). Resolves the beneficiary entity by exact ΑΦΜ via `services/entity_resolution` |
| `resolve.py` | `resolve_funding_link_for_act()` — join hierarchy **Levels 1-4**, tried strictly in order, stopping at the first that yields exactly one unambiguous candidate |

Wired into the ΚΗΜΔΗΣ CLI as `--with-anaptyxi --anaptyxi-period <period>`
(**opt-in**, needs the matching base-URL env var; period defaults to
2014-2020). Triggered per `ActUpsertResult.funding_ref_candidates` (Level
1) and/or `contractor_afm_normalized`/the act's own buyer ΑΦΜ (Levels 2-4)
— whichever data is available; `cli.py` fetches the act's own
title/date/amount/region from the DB since `ActUpsertResult` doesn't carry
them directly (`related_ada` it already exposes, reused for Level 3).

## The §19.4 "critical correction", made concrete

ΚΗΜΔΗΣ carries two candidate funding-reference fields
(`publicFundingRefOps`, `espaFundProgramRef`) and neither is assumed to be
the ΟΠΣ/MIS code without evidence. `resolve_funding_link_for_act()` tries
both, in order, as candidate MIS values against the real ΑΝΑΠΤΥΞΗ API;
whichever one actually resolves to a real project is recorded in
`funding_links.evidence` (`{"matched_field": ..., "mis_value": ...}`) — so
which field's semantics were confirmed is visible per record, not assumed
globally. Because this is still an inference about field semantics rather
than a source-asserted relationship, the recorded confidence is **0.95**,
not 1.0 (contrast with `adamChain`/Διαύγεια exact-ΑΔΑ links, which get 1.0
because the source itself states the relationship).

## Join hierarchy (§19.2) — all 4 levels implemented

1. **Exact ΟΠΣ/MIS code** (`MIS_OPS_EXACT`, confidence 0.95) — tries both
   candidate ΚΗΜΔΗΣ funding-reference fields against the real API in order
   (the §19.4 "critical correction": neither field's semantics are assumed
   without evidence).
2. **Beneficiary/contractor ΑΦΜ + project title + time period**
   (`AFM_TITLE_PERIOD`, confidence 0.85) — searches
   `find_projects_by_beneficiary_afm()`, matches by title similarity
   (`services/entity_resolution/text_similarity.py`, ≥0.5) and period
   overlap (±60 days slack) on a single unambiguous candidate.
3. **ΑΔΑ/ΑΔΑΜ found in metadata** (`ADA_ADAM_IN_METADATA`, confidence 0.90)
   — same candidate set as Level 2, matched instead by scanning each
   candidate's raw metadata for containment of one of the act's own
   referenced ΑΔΑ values (`ActUpsertResult.related_ada`). "...or documents"
   half of §19.2's wording isn't reachable — needs the documents pipeline,
   still out of scope generally.
4. **Normalized title + similar amount + same region + same beneficiary,
   with mandatory review** (`FUZZY_TITLE_AMOUNT_REGION`, confidence 0.60)
   — same candidate set again, looser title threshold (≥0.35), ±15%
   amount tolerance, region checked only when both sides carry a NUTS
   code (never required — ΑΝΑΠΤΥΞΗ's API isn't known to expose a general
   region search). Left with `funding_links.reviewed_by IS NULL` — the
   review-queue signal (mirrors `act_links.reviewed_by`'s existing
   convention; no separate review-queue table was built for this).

Levels 2-4 share **one** beneficiary/contractor-ΑΦΜ-scoped search call
(`find_projects_by_beneficiary_afm`) rather than three separate ones — a
scoping simplification given ΑΝΑΠΤΥΞΗ's Open Data API isn't known to expose
a general full-text/region search endpoint independent of ΑΦΜ.

A ΚΗΜΔΗΣ act whose funding-reference fields don't resolve at *any* level is
left unlinked rather than guessed at — deferred, not silently dropped.

## Not yet implemented

Per-payment detail (only aggregate `contracted_amount`/`paid_amount`, per
the source-mapping doc's known simplification), subproject-level records,
a dedicated review-queue UI/workflow beyond the `reviewed_by IS NULL`
marker (querying `funding_links WHERE confidence < 0.85 AND reviewed_by IS
NULL` today doubles as the review queue).
