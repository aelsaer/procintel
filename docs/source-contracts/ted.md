# Source contract: TED Search API

Spec refs: `description.txt` §3.8, §21.

## Role

European-level publication and comparison (P2): identifying Greek contracts
published EU-wide, cross-country comparison, European buyer/supplier
analysis, CPV market sizing, value/procedure comparison, eForms enrichment.

## Access

TED Search API v3. Search endpoint, bulk XML download. **No authentication
required** for the Search API.

## Version-aware parsing — mandatory

Do not attempt a single parser without version awareness:

```
TED parser
  ├── legacy forms
  ├── eForms version A
  ├── eForms version B
  └── unknown-version quarantine
```

Every normalized field must record `source_path`, `parser_version`, and
`confidence` (`ted_notice_details` + `field_provenance`).

## Fields captured

Raw XML/JSON, TED notice ID, publication number, eForms version, notice type,
buyer, supplier, CPV, lots, estimated/awarded value, procedure, dates,
country + NUTS, related notices.

## Linkage to ΚΗΜΔΗΣ (§21.3), in order

1. Exact TED notice ID.
2. Explicit reference in a document.
3. Buyer VAT + publication date + CPV.
4. Buyer name + title + amount + date.
5. Manual review.
