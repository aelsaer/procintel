# services/analytics

Procurement market metrics and tenant-relative opportunity scoring from
`description.txt` §27. The service never presents anomaly indicators as proof
of corruption or a tenant fit score as a win probability.

## Implemented intelligence

- Recorded market value/count, supplier share and HHI.
- Buyer concentration and supplier dependency.
- Incumbent and renewal signals.
- Contract modification rate/value uplift.
- Procurement cycle time and payment execution coverage.
- Procedure mix, supplier trends, funding summary and relationship data exposed
  by the intelligence API.
- Tenant-specific opportunity scoring with CPV fit, buyer affinity, timing,
  competitive attractiveness, value fit, data confidence and evidence bullets.
- Top-buyer ranking (`GET /v1/analytics/top-buyers`), the buyer-side
  counterpart to the pre-existing `top-suppliers` — same current-acts-only,
  taxonomy/date/geo-filtered query shape, ranked by recorded awarded value.
- Risk and anomaly indicators (§28, `risk_indicators.py`,
  `GET /v1/intelligence/risk-indicators`) — see below.
- Region drill-down (`GET /v1/analytics/region-activity`) — the map's
  "what exists here" list: every act in a clicked NUTS region, same
  taxonomy/date/amount filter shape as `/opportunities` but *not* restricted
  to opportunity act types (`REQUEST`/`APPROVED_REQUEST`/`NOTICE`) — a
  region click is about browsing everything recorded there, contracts
  included, so an optional `act_types` param (comma-separated) narrows
  instead of a fixed `WHERE act_type IN (...)`.

## Risk and anomaly indicators (§28)

`risk_indicators.py` implements seven of the twelve indicator types §28 names,
each backed by data this platform actually has: `HIGH_BUYER_CONCENTRATION`,
`REPEAT_SAME_CONTRACTOR`, `FEW_DISTINCT_SUPPLIERS`, `REPEATED_MODIFICATIONS`,
`LARGE_VALUE_INCREASE`, `UNUSUAL_AWARD_TO_CONTRACT_DELAY`,
`COMPANY_INACTIVE_IN_LATER_SNAPSHOT`. §28 is explicit that this is a
*procurement pattern* product, not an accusation engine — every instance
carries the mandated non-accusatory UI copy ("Εντοπίστηκε ασυνήθιστο μοτίβο
που απαιτεί περαιτέρω εξέταση", never "corruption detected") plus its
mathematical definition, benchmark, minimum sample size, confidence,
sources and known limitations, per §28's explicit per-indicator disclosure
requirement.

The other five §28 indicator types are deliberately not attempted yet:
short submission deadlines and consecutive awards near statutory thresholds
need data fields/reference tables this platform doesn't confirm having;
general historical-benchmark deviation and possible-fragmentation clustering
need an analysis method (time-series baselining, similarity clustering)
beyond a first pass; "missing expected linked acts" wasn't scoped in this
pass. Not guessed — see the module's own docstring.

Migration 22 makes market marts use the source-native event-date fallback and a
deterministic CPV when the provider did not mark one primary. It also aggregates
each contract once per market dimension, preventing party/location joins from
multiplying value.

## Workers

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel

python -m services.analytics.cli refresh-marts
python -m services.analytics.cli score-queued

# Administrative/manual full scoring pass
python -m services.analytics.cli score-opportunities --all-tenants
```

Business-profile updates enqueue tenant scoring automatically. `refresh-marts`
uses a PostgreSQL advisory lock and records start/finish/error state. It is also
part of ingestion orchestration unless `--no-marts` is supplied.

## Coverage semantics

Metrics retain nulls when required links or identities are absent. HHI is only
meaningful with supplier identities and values; payment execution needs linked
payment acts; cycle time needs high-confidence lifecycle links. The UI exposes
methodology and coverage instead of filling these gaps with guesses.

Most materialized views currently refresh non-concurrently because they do not
all have the unique indexes PostgreSQL requires for concurrent refresh.
