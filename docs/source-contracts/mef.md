# Source contract: Μητρώο Επιχορηγούμενων Φορέων (ΜΕΦ)

Spec refs: `description.txt` §3.5, §20.

## Role

Specialized view of subsidies and declared expenses (P2) — organizations,
expenses, revenues. Expenses can include entity ΑΦΜ, recipient ΑΦΜ, name,
amount, VAT, date, and related ΑΔΑ.

## What it is not

**Not** a universal registry of actual Greek public-sector payments. It
covers specific categories of subsidized entities only, and late/retroactive
submission of records has been observed. Every metric built on it must show
coverage and last-updated date (`mef_expenses`/`mef_organizations` joined
against `data_quality`/freshness fields — never presented as complete).

## Join policy

**Do not** link solely on `ΚΗΜΔΗΣ contractor AFM = MEF recipient AFM` — the
same company can have many unrelated transactions. Strong linkage requires a
combination:

```
ΑΦΜ + ΑΔΑ + amount + date + buyer
```

| Combination | Confidence |
|---|---|
| Same ΑΔΑ + same ΑΦΜ | 0.99 |
| Same ΑΔΑ + same buyer | 0.97 |
| Same ΑΦΜ + same amount + ±5 days | 0.90 |
| Same ΑΦΜ only | Candidate only, **not** a link |

## UI wording (enforced at the API/presentation layer, not just here)

Not: *"The contract was paid."*
Instead: *"A declared expense or payment order was found that possibly
relates to the contract."* — unless the link and the source's own semantics
fully prove it (`mef_expenses.linked_act_id` comment in
`db/migrations/05_funding_and_external_sources.sql`).
