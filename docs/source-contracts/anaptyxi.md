# Source contract: ΑΝΑΠΤΥΞΗ.gov.gr

Spec refs: `description.txt` §3.4, §19.

## Role

ESPA-funded projects (P1): ΟΠΣ/MIS code, project/υποέργο, beneficiary,
budget, contracts, payments, contractors, regions, dates, status/progress.

## Programming periods — separate adapters, one canonical schema

```
ANAPTYXI_2007_2013
ANAPTYXI_2014_2020   -- clearest public Open Data API documentation found
ANAPTYXI_2021_2027   -- newer portal; BLOCKED_UPSTREAM until a project-level
                         public API contract is published and validated
```

All three converge on `funding_projects` (§19.3) — do not special-case
`program_period` outside the connector layer.

## API shape (2014-2020, best documented)

Daily updates, JSON as the primary format, pagination, and data for projects,
subprojects, beneficiaries, contractors, budgets, and payments.

Validated public deployments:

- `https://2013.anaptyxi.gov.gr` for 2007-2013.
- `https://anaptyxi.gov.gr` for 2014-2020.

The list response uses `Records` and project code `kodikos`. A list row is
only a discovery result. The connector always follows it with
`queryType=projectDetails&projectDetails=all` before canonical persistence,
so subprojects, bodies, geographic allocations, budgets and payment
snapshots are not silently omitted.

The legacy deployments sometimes return HTTP 200 with an empty or non-JSON
body for a missing project code. The connector treats this observed shape
as `ProjectNotFoundError`, then continues through the documented AFM
hierarchy; it is not recorded as a parser failure.

`https://2027.anaptyxi.gov.gr` is the official 2021-2027 portal, but its
observed `GetData.ashx` surface exposes aggregate chart requests rather than
the validated `projects_v2`/`projectDetails` contract. The provider remains
truthfully blocked upstream unless a validated project API URL is supplied
with `ANAPTYXI_2021_2027_API_BASE_URL`.

## Join hierarchy to ΚΗΜΔΗΣ (§19.2)

1. Exact ΟΠΣ/MIS code — **only** when the source field's semantics are
   confirmed for that record type.
2. Beneficiary/contractor ΑΦΜ + project title + time period.
3. ΑΔΑ or ΑΔΑΜ found in metadata or documents.
4. Normalized title + similar amount + same region + same beneficiary, with
   mandatory review when confidence isn't high.

## Critical correction

`ΚΗΜΔΗΣ.espaFundProgramRef = MIS` is **not** universally true — verify per
record. ΚΗΜΔΗΣ carries more than one funding-reference field
(`publicFundingRefOps`, `espaFundProgramRef`); store both and confirm which
one actually corresponds to the ΟΠΣ/MIS code for that record type.

## Payment semantics

The public detail contract exposes aggregate project and subproject payment
snapshots, not an itemized payment ledger. Both levels are persisted with
their source record and refresh timestamp; the UI must label them as
ΑΝΑΠΤΥΞΗ aggregate execution figures rather than individual payments.

## Company participation semantics

`searchField=4` and `searchField=6` are exact company-code searches. A hit
confirms project participation for the queried AFM, but a project detail may
contain several contractor names with no AFM per body. The canonical model
therefore stores the exact project-level relationship in
`funding_project_participations` and leaves ambiguous free-text body names
unresolved. Name-to-entity matching requires independent evidence.
