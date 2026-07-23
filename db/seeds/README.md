# db/seeds

Reference data seeds. Loaded automatically by `db/run_migrations.sh` (runs
`db/seeds/*.sql` last, after migrations and marts) — safe to re-run,
every insert uses `ON CONFLICT DO NOTHING`.

- `gemi_lexicons.sql` — ΓΕΜΗ legal-form/company-status vocabulary
  (`db/migrations/11_gemi_lexicons.sql`'s tables), mirroring
  `services/ingestion/connectors/gemi/lexicon.py`'s Python dicts (keep both
  in sync by hand). **Implemented.**

Still placeholders, not yet built: CPV code hierarchy, NUTS areas, license
codes, alert event types.
