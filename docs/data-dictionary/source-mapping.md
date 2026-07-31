# Source → Canonical Field Mapping

Per-source mapping from raw API fields to canonical schema (`db/migrations/*`).
This is the concrete artifact behind "include information from all the
platforms for every procurement" — it is what `services/ingestion/normalization`
implements and what `procurement_360` (`db/marts/procurement_360.sql`) reads
back out. Confidence tiers reference the hierarchy in `description.txt` §8.

## ΚΗΜΔΗΣ (spec §3.1, §16)

| Raw field | Canonical target | Notes |
|---|---|---|
| `referenceNumber` (ΑΔΑΜ) | `act_identifiers(scheme='ADAM')`, `procurement_acts.source_record_id` chain | Uppercased, trimmed, never fuzzy-matched (§7.2) |
| `title` | `procurement_acts.title` / `normalized_title` | |
| `submissionDate` | `procurement_acts.submission_date` | |
| `organizationVatNumber` | `entity_identifiers(scheme='AFM')` on buyer entity → `act_parties(party_role='BUYER')` | Checksum-validated (§7.2); failed checksum sets `identifier_valid=false`, `match_eligibility='RESTRICTED'` on the identifier, not a rejected record |
| `cpvItems` | `act_cpv_codes.cpv_code` | |
| `nutsCode` / `nutsCodes` | `act_locations.nuts_code` | |
| `commitmentNo` | `act_identifiers`, triggers `FETCH_DIAVGEIA_DECISION` job | |
| `decisionRelatedAda` | `act_identifiers(scheme='ADA')`, triggers Διαύγεια direct fetch | confidence 1.0, `link_method='EXACT_ADA'` |
| `contractRelatedAda` / `contractRelatedADA` | same as above | Casing drift handled by the normalization layer's canonical field-name mapping (§3.1) before it ever reaches `act_identifiers` |
| `cancellationADA` | `act_identifiers(scheme='ADA')` on a `CANCELLATION` act, `act_links(link_type='CANCELS')` | |
| `aaht` | `entity_identifiers(scheme='AAHT')` on buyer entity | Useful key, never sole master identifier (§7.2) |
| `fundingDetails` | kept in `source_records.payload_uri` raw JSON; parsed sub-fields below | |
| `publicFundingRefOps` | candidate `funding_links` join key | verified against `funding_projects.mis_ops_code`, not assumed equal |
| `espaFundProgramRef` | candidate `funding_links` join key | **not** assumed = MIS/OPS without verification (§19.4 correction) |
| `regularBudgetFundedProgramRef` | stored raw only | regular-budget funding, not ESPA — not joined to `funding_projects` |
| `selfFundProgramRef` | stored raw only | self-funded — not joined to `funding_projects` |
| στοιχεία αναδόχων (awardee info) | `act_parties(party_role='SUPPLIER'/'CONTRACTOR')`, `entities`, `entity_identifiers(scheme='AFM')` | |
| ποσά | `procurement_acts.amount_net` / `amount_gross`, `act_parties.amount` | |
| ΦΠΑ | `procurement_acts.vat_amount` | |
| ημερομηνίες | `procurement_acts.{publication,submission,decision,start,end}_date` | |
| είδος διαδικασίας | `procurement_acts.procedure_type` | |
| στοιχεία τροποποίησης | new `procurement_acts` row (`act_type='AMENDMENT'`) + `act_links(link_type='AMENDS')` | |
| σχετικά ΑΔΑΜ/ΑΔΑ (via `adamChain`) | `act_links(link_method='ADAMCHAIN', confidence=1.000)`, `process_members` | Primary lifecycle-linkage source (§2, §16.5) |

## Διαύγεια (spec §3.2, §17)

| Raw field | Canonical target | Notes |
|---|---|---|
| ΑΔΑ | `act_identifiers(scheme='ADA')` on a `DIAVGEIA_DECISION` act | |
| θέμα (subject) | `procurement_acts.title` | |
| τύπος πράξης | stored in raw payload + `field_provenance`; no dedicated column | low-cardinality enough to leave in raw JSON for v1 |
| εκδούσα αρχή | `act_parties(party_role='SIGNER_ORGANIZATION')` → `entities` | |
| οργανωτική μονάδα | raw payload + `field_provenance` | |
| υπογράφοντες (signers) | `entities(entity_type='PERSON')`, minimal fields only (§41.2), linked via `act_parties(party_role='SIGNER_PERSON')` | Distinct from `SIGNER_ORGANIZATION` (the deciding org unit) |
| document URL / PDF | `documents.source_url` / `object_uri`, `act_id` = the decision act | |
| version log | `source_records` chain (`is_latest`) + `act_links(link_type='SUPERSEDES')` between decision versions | |
| related decisions | `act_links(link_type='RELATED_TO')` | |

## ΓΕΜΗ (spec §3.3, §18)

| Raw field | Canonical target | Notes |
|---|---|---|
| επίσημη επωνυμία | `entity_company_snapshots.official_name` | |
| διακριτικός τίτλος | `entity_company_snapshots.trade_name` | |
| αριθμός ΓΕΜΗ | `entity_identifiers(scheme='GEMI')`, `entity_company_snapshots.gemi_number` | |
| ΑΦΜ | `entity_identifiers(scheme='AFM')` | |
| νομική μορφή | `entity_company_snapshots.legal_form` / `legal_form_code` | lexicon seeded in `db/seeds` |
| κατάσταση επιχείρησης | `entity_company_snapshots.company_status` | temporal — never overwritten (§18.2) |
| αρμόδια υπηρεσία ΓΕΜΗ | `entity_company_snapshots.gemi_office` | |
| ημερομηνία απόδοσης αριθμού ΓΕΜΗ | `entity_company_snapshots.gemi_registration_date` | |
| ΚΑΔ | `entity_company_snapshots.kad_codes` | |
| δήμο / νομό | `entity_company_snapshots.municipality` / `region`, `entity_addresses` | |
| δημόσια έγγραφα, ανακοινώσεις | `documents` (`act_id` NULL, keyed via `source_record_id`) | |

## ΑΝΑΠΤΥΞΗ.gov.gr (spec §3.4, §19)

| Raw field | Canonical target | Notes |
|---|---|---|
| κωδικός ΟΠΣ/MIS | `funding_projects.mis_ops_code` | join hierarchy: exact MIS (only if field semantics confirmed) → ΑΦΜ+title+period → ΑΔΑ/ΑΔΑΜ in metadata → fuzzy+review (§19.2) |
| Πράξη / Υποέργο | `funding_projects` / `funding_subprojects` | Full project detail is hydrated before persistence |
| δικαιούχος | `funding_projects.beneficiary_id` → `entities` | |
| προϋπολογισμός | `funding_projects.budget` | |
| συμβάσεις | `funding_links` → `procurement_acts` | |
| πληρωμές | `funding_projects.paid_amount`, `funding_subprojects.paid_amount`, `funding_payment_snapshots` | Aggregate project/subproject execution snapshots, not bank transactions |
| ανάδοχοι | `funding_project_bodies`, `funding_project_participations` | Exact AFM query proves project participation; ambiguous free-text body names remain unresolved |
| περιφέρειες | `funding_geographic_allocations` | Region, prefecture, municipality, amount and percentage where exposed |
| ημερομηνίες, κατάσταση/πρόοδος | `funding_projects.{start_date,end_date,status}` | separate adapters per period (`program_period`), one schema (§19.3) |

## Μητρώο Επιχορηγούμενων Φορέων / ΜΕΦ (spec §3.5, §20)

| Raw field | Canonical target | Notes |
|---|---|---|
| organizations | `mef_organizations` | `entity_id` resolved where possible |
| ΑΦΜ φορέα | `mef_organizations.afm_raw` | |
| ΑΦΜ λήπτη, επωνυμία, ποσό, ΦΠΑ, ημερομηνία, σχετικά ΑΔΑ | `mef_expenses.{recipient_afm_raw, amount, vat_amount, expense_date, related_ada_raw}` | |
| link to ΚΗΜΔΗΣ contract | `mef_expenses.linked_act_id` | Never linked on ΑΦΜ alone (§20.1); confidence tiers: ΑΔΑ+ΑΦΜ=0.99, ΑΔΑ+buyer=0.97, ΑΦΜ+amount±5d=0.90, ΑΦΜ-only=candidate (no link row) |

## TED (spec §3.8, §21)

| Raw field | Canonical target | Notes |
|---|---|---|
| raw XML/JSON | `source_records.payload_uri` | |
| TED notice ID | `ted_notice_details.ted_notice_id`, `act_identifiers(scheme='TED_NOTICE_ID')` | |
| publication number | `ted_notice_details.publication_number` | |
| eForms version | `ted_notice_details.eforms_version` (NULL/empty ⇒ legacy forms) | version-aware parser, no single parser (§21.2) |
| notice type | `ted_notice_details.notice_type` | |
| buyer / supplier | `ted_notice_details.buyer_raw` / `supplier_raw` (full payload) + resolved `act_parties`/`entities` where matched with sufficient confidence | |
| CPV | `act_cpv_codes` on the `TED_NOTICE` act | |
| lots | `ted_notice_details.lots` (JSONB) + `procurement_lots` where matched to a ΚΗΜΔΗΣ process | |
| estimated/awarded value | `procurement_acts.{amount_net or amount_gross}` on the `TED_NOTICE` act | |
| procedure | `procurement_acts.procedure_type` | |
| dates | `procurement_acts.{publication_date, decision_date, ...}` | |
| χώρα / NUTS | `ted_notice_details.country_code` / `nuts_codes` | |
| related notices | `ted_notice_details.related_notice_ids`, `act_links(link_type='RELATED_TO')` | |
| link to ΚΗΜΔΗΣ | `act_links` | order: exact TED notice ID → explicit document reference → buyer VAT+publication date+CPV → buyer name+title+amount+date → manual review (§21.3) |

## VIES (spec §3.9, §7.2)

| Raw field | Canonical target | Notes |
|---|---|---|
| country + VAT number | `entity_vies_checks.country_code` / `national_number` / `normalized_eu_vat` | |
| validity result | `entity_vies_checks.vies_valid` | validator only — never treated as a company profile (§3.9) |
| check timestamp | `entity_vies_checks.checked_at` | |
| raw response hash | `entity_vies_checks.vies_response_hash` | |

## data.gov.gr / CKAN + INSPIRE (spec §3.6, §3.7, §22)

| Raw field | Canonical target | Notes |
|---|---|---|
| `package_search` / `package_show` / `resource_search` results | `external_datasets` | catalogue registry, not an operational store (§3.6) |
| onboarded dataset resources (administrative boundaries, population, schools, hospitals, regional indicators, environmental layers) | `administrative_boundaries`, `geo_denominators`, `nuts_areas.geom` | one `adapter_name` + `config` per onboarded dataset in `external_datasets`, applied per-dataset (§22.2) |
| INSPIRE geometries | `administrative_boundaries.geom` / `nuts_areas.geom` | first cut: administrative boundaries, NUTS, postal codes only — no cadastral parcels in v1 (§3.7) |
