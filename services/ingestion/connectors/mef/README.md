# connectors/mef

Μητρώο Επιχορηγούμενων Φορέων connector (spec §3.5, §20). Not a universal public
payments registry — covers specific subsidized-entity categories only, with
possible late/retroactive submissions. Strong linkage requires ΑΦΜ + ΑΔΑ + amount
+ date + buyer combined (ΑΦΜ alone is a candidate, never an auto-link). Every
derived metric must show coverage and last-updated date. See
`docs/source-contracts/mef.md`.

## Status: implemented

- `MefClient` calls the published `/api/spendings` endpoint with
  `searchTerm`, `limit`, and `offset`, then exact-filters `issuer_afm`.
- The normalizer handles the API's dotted organization fields, Greek
  `DD/MM/YYYY` dates, and Greek-formatted decimal amounts.
- Contractor lookups are rate limited, paginated, and cached per ΑΦΜ for
  one day before tiered linkage is attempted.
- `MEF_API_BASE_URL` is only an override; the public production base is the
  default.
