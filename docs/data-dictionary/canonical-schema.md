# Canonical Schema — Data Dictionary

Generated from `db/migrations/*.sql` and `db/marts/*.sql`. If this ever
disagrees with the SQL, the SQL wins — update this file to match.

## 01 — Source layer (`01_extensions_and_source_layer.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `source_records` | Append-only store of every distinct payload version fetched from any source. Never updated in place. | `source_system`, `resource_type`, `content_sha256` (unique per source+resource), `payload_uri`, `is_latest`, `parse_status` |
| `connector_runs` | One row per ingestion partition attempt — success rate, retries, freshness. | `source_system`, `resource_type`, `partition_key`, `status`, `records_upserted` |
| `source_cursors` | Watermark per (source, resource, partition); only advances on full-partition success. | PK `(source_system, resource_type, partition_key)`, `cursor_value` |
| `external_datasets` | Registry of onboarded CKAN/data.gov.gr/INSPIRE datasets. | `catalog_source`, `catalog_dataset_id`, `ingestion_status` |

## 02 — Identity & registry (`02_identity_and_registry.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `entities` | Canonical PUBLIC_ORGANIZATION / COMPANY / CONSORTIUM / FUNDING_PROGRAM / PROJECT / PERSON / GEOGRAPHIC_AREA. | `entity_type`, `canonical_name`, `status` (ACTIVE/MERGED), `merged_into_id` |
| `entity_identifiers` | The central identifier registry: ADAM, ADA, AFM, EU_VAT, GEMI, AAHT, CPV, NUTS, MIS_OPS, TED_NOTICE_ID, SOURCE_NATIVE_ID. | `scheme`, `value_normalized`, `confidence`, `identifier_valid`, unique per (scheme, country, value) while current+confidence=1 |
| `entity_names` | Alternate/historical names, including per-source raw spellings. | `name_type`, `normalized_name`, `name_without_legal_form` |
| `entity_addresses` | Temporal registered addresses with geometry. | `nuts_code`, `geom`, `is_current` |
| `entity_company_snapshots` | ΓΕΜΗ temporal state — legal form, status, ΚΑΔ; never overwritten. | `gemi_number`, `company_status`, `observed_at`, `is_current` |
| `entity_vies_checks` | VIES validation history (validator, not a profile). | `normalized_eu_vat`, `vies_valid`, `checked_at` |
| `entity_match_candidates` | Entity-resolution review queue (§25.4). | `score`, `score_breakdown`, `status` |
| `entity_merge_log` | Audit trail behind `entities.merged_into_id`; every merge reversible. | `surviving_entity_id`, `merged_entity_id`, `evidence`, `reverted_at` |

## 03 — Procurement core (`03_procurement_core.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `procurement_processes` | The business event: one procurement from request to payment. `public_id` never changes, even across merges. | `lifecycle_status`, `record_status`, `merged_into_process_id`, `buyer_entity_id` |
| `process_members` | Explicit act membership in a process (avoids "first ΑΔΑΜ = process_id"). | `(process_id, act_id)` unique |
| `process_merge_log` | Reversible process-merge audit trail. | mirrors `entity_merge_log` |
| `procurement_acts` | Every published act as an independent row: REQUEST, APPROVED_REQUEST, NOTICE, AWARD, CONTRACT, AMENDMENT, CANCELLATION, PAYMENT, DIAVGEIA_DECISION, TED_NOTICE. | `act_type`, `agreement_type` (incl. FRAMEWORK_AGREEMENT/CALL_OFF), `framework_ceiling_amount`, `source_record_id` |
| `act_identifiers` | Identifiers scoped to one act. | `scheme`, `value_normalized`, unique per (scheme, value) |
| `act_links` | Act-to-act edges: APPROVES, ANNOUNCES, AWARDS, EXECUTES, AMENDS, EXTENDS, CANCELS, PAYS, FUNDED_BY, PUBLISHED_AS, SUPERSEDES, RELATED_TO. | `link_method`, `confidence`, `evidence` |
| `act_parties` | Who's involved and in what role: BUYER, CONTRACTING_AUTHORITY, SUPPLIER, CONTRACTOR, CONSORTIUM_MEMBER, BENEFICIARY, FUNDING_AUTHORITY, DONOR, RECIPIENT, SIGNER_ORGANIZATION, SIGNER_PERSON. | `entity_id`, `party_role`, `lot_id`, `amount` |
| `act_cpv_codes` | CPV classification per act, optionally per lot. | `(act_id, cpv_code)` PK, `is_primary` |
| `act_locations` | Execution location (distinct from party registered address). | `nuts_code`, `geom` |
| `procurement_lots` | Multi-lot procedures. | `estimated_value`, `awarded_value` |

## 04 — Documents & provenance (`04_documents_and_provenance.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `documents` | Stored files (originals + extraction status). | `object_uri`, `sha256` (unique), `text_extraction_status` |
| `field_provenance` | Per-field lineage for any canonical object: which source record, path, extraction method, confidence. Backs the evidence drawer. | `object_type`, `object_id`, `field_name`, `source_record_id`, `confidence` |

## 05 — Funding & external sources (`05_funding_and_external_sources.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `funding_projects` | ΑΝΑΠΤΥΞΗ project/υποέργο, one schema across 2007-2013/2014-2020/2021-2027 adapters. | `mis_ops_code`, `program_period`, `budget`, `contracted_amount`, `paid_amount` |
| `funding_links` | Contract ↔ funding project, with join-hierarchy method (§19.2). | `link_method`, `confidence`, `evidence` |
| `ted_notice_details` | 1:1 supplement to a TED_NOTICE act; version-aware (eForms A/B/legacy). | `eforms_version`, `parser_version`, `parse_confidence` |
| `mef_organizations` | ΜΕΦ-side organization record, resolved to `entities` where possible. | `entity_id`, `afm_raw` |
| `mef_expenses` | ΜΕΦ declared expense/payment order — a *signal*, never asserted as "the contract was paid" without full proof. | `linked_act_id`, `link_method`, `confidence` (tiers per §20.2) |

## 06 — Reference & geo (`06_reference_geo.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `cpv_codes` | CPV hierarchy: 8-digit code, check digit, 2/3/4/5-digit prefixes, EL/EN descriptions. | `code` PK, `parent_code` |
| `nuts_areas` | NUTS 0-3 with geometry. | `level`, `classification_version`, `geom` |
| `administrative_boundaries` | Municipal/regional boundaries, postal codes, selected thematic zones (INSPIRE/data.gov.gr). | `boundary_type`, `geom` |
| `geo_denominators` | Population/students/beds etc. behind per-capita metrics (§22.3), always dated and sourced. | `metric_name`, `reference_year`, `value` |

Note: `act_cpv_codes.cpv_code`, `act_locations.nuts_code`, `entity_addresses.nuts_code`
are **soft** references to these tables (no hard FK) — ingestion must not
hard-fail on a code the reference table hasn't caught up with; gaps surface
via `data_quality_issues` instead.

## 07 — Data quality (`07_data_quality.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `data_quality_issues` | Quarantine/issue log across the pipeline. | `issue_code`, `severity` (INFO/WARNING/ERROR/BLOCKING), `status` |

## 08 — Multi-tenancy & workspace (`08_multitenancy_and_workspace.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `tenants` | Customer accounts. | `plan` (STARTER/PROFESSIONAL/ENTERPRISE/DATA_API) |
| `users` | Global user identities. | `is_internal_reviewer` |
| `tenant_memberships` | Tenant-scoped roles: OWNER, ADMIN, ANALYST, SALES, BID_MANAGER, VIEWER, API_CLIENT. | `(tenant_id, user_id)` unique |
| `api_keys` | Hash-only API key storage. | `key_hash`, `scopes`, `expires_at` |
| `audit_log` | Login/export/key-creation/merge/manual-link/admin-access trail. | `action`, `object_type`, `object_id` |
| `saved_searches` | Per-user saved query filters. | `query` JSONB |
| `opportunity_pipeline_items` | Tenant's tracked opportunities. | `process_id`, `stage` |
| `notes` | Free-text notes on any canonical object, tenant-private. | `object_type`, `object_id` |
| `tags` / `tag_links` | Tenant-scoped tagging of any object. | `(tenant_id, name)` unique |

Canonical tables (entities, processes, acts, ...) stay globally readable;
tenant-scoped tables above enforce isolation via row-level security keyed on
session `tenant_id` (sketch included in the migration; policies land with the
auth layer).

## 09 — Alerts (`09_alerts.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `alert_rules` | User-defined rule: event types, filters, schedule, channels. | `event_types`, `filters`, `delivery_channels` |
| `alert_events` | Deduplicated firing log. Dedup key = rule + object + event type + material-change hash. | unique `(alert_rule_id, canonical_object_id, event_type, material_change_hash)` |
| `webhook_deliveries` | Delivery/retry tracking with idempotency key + signature. | `idempotency_key` unique per tenant, `status` |

## 18 — Competitor participation (`18_competitor_participations.sql`)

| Table | Purpose | Key fields |
|---|---|---|
| `process_participations` | Evidence-backed bidder, winner and consortium-member facts. Inferred market competitors are never persisted as participation. | `process_id`, `entity_id`, `participation_role`, `evidence_type`, `confidence`, `document_id`, `source_page`, `evidence_key` |

`OFFICIAL_SOURCE` and `DOCUMENT_EXTRACTED` facts retain source lineage. The
document path requires an explicit procurement role adjacent to an ΑΦΜ; bare
company names and bare tax numbers are not treated as participation evidence.

## `db/marts` — Read models & analytics

| Object | Type | Purpose |
|---|---|---|
| `procurement_360` | VIEW | Unified per-process record aggregating all sources — see `docs/architecture/overview.md` §4. |
| `market_value_metrics` | MATERIALIZED VIEW | Market size/count/avg/median by (CPV prefix-4, NUTS, year, procedure type), §27.1. |
| `supplier_market_share` | MATERIALIZED VIEW | Per-supplier value/count within a market key, §27.2. |
| `market_hhi` | MATERIALIZED VIEW | Herfindahl-Hirschman concentration index per market, §27.3. |
| `buyer_concentration` | MATERIALIZED VIEW | Top-supplier share per buyer, §27.4. |
| `supplier_dependency` | MATERIALIZED VIEW | Supplier's recorded-revenue dependency on each buyer, §27.5. |
| `incumbent_signals` | MATERIALIZED VIEW | Likely-incumbent heuristic with evidence, §27.6. |
| `contract_modification_stats` | MATERIALIZED VIEW | Modification rate + value uplift per contract, §27.7-27.8. |
| `cycle_time_metrics` | MATERIALIZED VIEW | Request→notice→award→contract→first-payment day counts, high-confidence links only, §27.9. |
| `payment_execution` | MATERIALIZED VIEW | Linked payment / contract value with coverage badge, §27.10. |
| `renewal_signals` | MATERIALIZED VIEW | Renewal-watch window combining remaining days and buyer lead time, §27.11. |
| `opportunity_scores` | TABLE (app-computed) | Explainable, tenant-scoped opportunity score with sub-score breakdown, §27.12. |
