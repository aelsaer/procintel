# Source contract: VIES

Spec refs: `description.txt` §3.9, §7.2.

## Role

EU VAT validation only (P2): validity check, country confirmation, check-date
storage, auxiliary validation of foreign suppliers.

## What it is not

Not a company profile source. Never used to populate `entity_company_snapshots`
— only `entity_vies_checks`.

## Access

WSDL `checkVat` operation; validates VAT numbers through the member-state
systems.

## Fields stored (`entity_vies_checks`)

```
country_code
national_number
normalized_eu_vat
last_vies_check_at   -> checked_at
vies_valid
vies_response_hash
```
