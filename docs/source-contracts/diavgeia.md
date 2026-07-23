# Source contract: Διαύγεια OpenData

Spec refs: `description.txt` §3.2, §17.

## Role

Administrative decisions and documents behind procurement acts, keyed by ΑΔΑ
(P0).

Base URL: `https://diavgeia.gov.gr/opendata`.

## Capabilities & degraded mode

Διαύγεια's public search is currently reported as running in a limited
maintenance mode. The connector must expose per-capability status so the rest
of the pipeline can react instead of blocking:

| Capability | Status enum |
|---|---|
| `DIRECT_ADA_FETCH` | AVAILABLE / DEGRADED / DISABLED / UNKNOWN |
| `SEARCH` | AVAILABLE / DEGRADED / DISABLED / UNKNOWN |
| `ADVANCED_SEARCH` | AVAILABLE / DEGRADED / DISABLED / UNKNOWN |
| `ORGANIZATION_LOOKUP` | AVAILABLE / DEGRADED / DISABLED / UNKNOWN |
| `SIGNER_LOOKUP` | AVAILABLE / DEGRADED / DISABLED / UNKNOWN |
| `VERSION_LOG` | AVAILABLE / DEGRADED / DISABLED / UNKNOWN |

**Strategy**: direct ΑΔΑ fetch is the primary, always-attempted path. General
search is supplementary backfill only — it must never block direct fetch when
Διαύγεια search is degraded.

Documented direct fetch path: `GET /decisions/:ada`.

## Trigger

Every time an ΑΔΑ appears in ΚΗΜΔΗΣ (`commitmentNo`, `decisionRelatedAda`,
`contractRelatedAda`/`contractRelatedADA`, `cancellationADA`), enqueue:

```json
{
  "job_type": "FETCH_DIAVGEIA_DECISION",
  "ada": "ΑΔΑ...",
  "origin_source": "KHMDHS",
  "origin_act_id": "uuid",
  "priority": "HIGH"
}
```

## Data captured

Decision metadata, subject, type, date, protocol number, issuing authority,
organizational unit, signers, document URL, PDF, version log, related
decisions where provided.

## Linkage confidence

| Method | Confidence |
|---|---|
| Exact ΑΔΑ (`EXACT_ADA`) | 1.0 |
| Title/organization search match (`DIAVGEIA_SEARCH_MATCH`) | < 1.0, requires multiple corroborating attributes |

## Known caveats

- Do not attempt a full Διαύγεια crawl as the first strategy — start from
  ΑΔΑ values already surfaced by ΚΗΜΔΗΣ.
- Individual signer persons need minimal-field `PERSON` entities per §41.2 —
  see the flagged gap in `docs/data-dictionary/source-mapping.md` (Διαύγεια
  section) about extending `act_parties.party_role`.
