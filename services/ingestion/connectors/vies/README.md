# connectors/vies

VIES connector (spec §3.9, §7.2). Validator only, not a company profile
source.

## Status: implemented

| Module | Purpose |
|---|---|
| `config.py` | `ViesConnectorConfig` — official European Commission SOAP service by default; `VIES_API_BASE_URL` is an optional override |
| `client.py` | `ViesClient.check_vat()` — builds the `checkVat` SOAP envelope, parses `<valid>true/false</valid>` out of the response. Unparseable responses resolve to `valid=None` (genuinely unknown), never coerced to `False` |
| `db_writer.py` | `record_vies_check()` — append-only `entity_vies_checks` row per check (a validation history, never a company profile — §3.9 is explicit about this) |
| `resolve.py` | `check_and_record_vies()` — the orchestration wrapper `connectors/ted` calls |

No caching/refresh policy — unlike ΓΕΜΗ's explicit §18.3 policy,
description.txt doesn't specify one for VIES, so every call is a real
check and a new row. Triggered only from `connectors/ted` when a TED
notice's supplier has `country_code != 'GR'` — Greek suppliers never reach
VIES (their identity is resolved by the Greek ΑΦΜ checksum instead, which
VIES can't validate anyway — it validates EU/foreign VAT numbers).

The trigger is deliberately limited to TED's foreign-supplier detection. VIES is
"auxiliary validation of foreign suppliers" per §3.9, and the only place
this codebase currently encounters a non-Greek supplier is TED notices.
