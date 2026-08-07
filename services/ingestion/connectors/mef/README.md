# connectors/mef

Μητρώο Επιχορηγούμενων Φορέων connector (spec §3.5, §20). Not a universal public
payments registry — covers specific subsidized-entity categories only, with
possible late/retroactive submissions. Strong linkage requires ΑΦΜ + ΑΔΑ + amount
+ date + buyer combined (ΑΦΜ alone is a candidate, never an auto-link). Every
derived metric must show coverage and last-updated date. See
`docs/source-contracts/mef.md`.

## Status: implemented

- `MefClient` calls the published `/api/spendings` endpoint with mandatory
  `year`, `searchTerm`, `limit`, and `offset`, then exact-filters
  `receiver_afm` (current envelope) or `issuer_afm` (documented legacy envelope).
  `MEF_LOOKUP_YEARS` can provide a comma-separated historical
  recovery scope; the safe daily default is the current UTC year.
- The normalizer handles the API's dotted organization fields, Greek
  `DD/MM/YYYY` dates, and Greek-formatted decimal amounts.
- Contractor lookups are rate limited, paginated, and cached per ΑΦΜ for
  one day before tiered linkage is attempted.
- `MEF_API_BASE_URL` is only an override; the public production base is the
  default.
- The provider's populated 2025 partition currently reports 351,753 rows,
  while non-zero offsets and year+AFM searches can time out. Such partitions
  remain retryable and visible in coverage; the worker never falls back to
  an unbounded all-years query.
- Exhausted transport, rate-limit, 4xx, or 5xx retries are reported as
  `BLOCKED_UPSTREAM`, never as a successful empty lookup. The worker stops
  MEF work for the rest of that sweep, continues every other provider, and
  retries MEF on a later sweep.
