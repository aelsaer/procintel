# packages/domain

Shared domain types mirroring the canonical schema so ingestion, linkage,
analytics and the API share one model instead of redefining it per service.

`tables.py` has SQLAlchemy Core definitions for the tables the ΚΗΜΔΗΣ
connector, API and alerts evaluator touch: `source_records`, `entities`,
`entity_identifiers`, `procurement_processes`, `procurement_acts`,
`act_identifiers`, `act_parties`, `act_cpv_codes`, `act_locations`,
`act_links`, `process_members`, `process_merge_log`, `tenants`, `users`,
`alert_rules`, `alert_events` — query-building metadata only, kept in sync
by hand with `db/migrations/*.sql`, which remains the DDL source of truth
(verified column-for-column, see `PROGRESS.md`). `tenants`/`users` are
minimal (just enough columns to satisfy `alert_rules`' FK constraints in
tests) — full multi-tenancy fields land whenever `apps/api` needs auth.
More tables get added here as later connectors/slices need them
(`procurement_lots`, funding, documents, ...), not speculatively.
