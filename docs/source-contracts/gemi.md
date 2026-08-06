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
- Authentication: `api_key` HTTP header, as declared by the official
  Swagger 2.0 document at `/api-docs`.
- Provider limit: **8 requests per rolling minute**. Configuration above 8
  is rejected at startup. API and scheduler share a file-backed rolling
  window under `RAW_STORE_ROOT/provider-limits/gemi.json`, so separate
  processes using the shared volume consume one combined provider budget.
- Every retry consumes a new rate-limit slot. `429` honors `Retry-After` and
  otherwise uses bounded backoff; `5xx` retries at most three times by
  default. `401` fails immediately with a credential error that never
  includes the configured key.

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
- The API key is a deployment secret. Keep it in an ignored local `.env` for
  development and in the production secret store; never commit it to an
  example file, image, log, or source record.
