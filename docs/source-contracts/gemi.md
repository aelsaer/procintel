# Source contract: Open Data ΓΕΜΗ

Spec refs: `description.txt` §3.3, §18.

## Role

Company identity enrichment (P1): official name, trade name, ΓΕΜΗ number,
ΑΦΜ, legal form, company status, responsible ΓΕΜΗ office, registration date,
ΚΑΔ, municipality/prefecture, public documents/announcements.

## Access

- Search by ΑΦΜ, ΓΕΜΗ number, name, ΚΑΔ, status, prefecture, municipality.
- Production use requires a personal API key request. **The MVP must not
  functionally depend on that approval** — implemented behind the
  `CompanyRegistryProvider` protocol (`packages/source_clients`) so a stub/
  mock provider can stand in until the key is granted.
- License: ODC-BY 1.0.

## Ingestion flow

```
new contractor ΑΦΜ
  → local cache check
  → checksum check
  → enqueue ΓΕΜΗ lookup
  → store raw response
  → update company profile (entity_company_snapshots)
  → create temporal snapshot (never overwrite)
```

## Cache policy

| Company state | Refresh cadence |
|---|---|
| New company | Immediate lookup |
| Active company | Every 30 days |
| Company in transition/change | More frequent |
| Public documents | Refresh based on last publication date |
| Negative result | Re-check later — **never** a permanent negative cache |

## Reference lexicons

Legal form codes, status codes, and geographic codes are stored locally in
`db/seeds` and refreshed periodically, not re-resolved per request.

## Known caveats

- Company status is temporal — always write a new
  `entity_company_snapshots` row rather than updating the current one, so
  "what was the company's status when the contract was signed" stays
  answerable.
