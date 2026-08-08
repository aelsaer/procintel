from pathlib import Path


def test_migration_ledger_primary_key_rejects_duplicate_history() -> None:
    migration = (
        Path(__file__).parents[2]
        / "db"
        / "migrations"
        / "46_migration_ledger_primary_key.sql"
    ).read_text(encoding="utf-8")

    assert "HAVING COUNT(*) > 1" in migration
    assert "ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (filename)" in migration


def test_existing_nonproduction_sources_are_quarantined_at_migration_time() -> None:
    migration = (
        Path(__file__).parents[2]
        / "db"
        / "migrations"
        / "48_quarantine_existing_synthetic_records.sql"
    ).read_text(encoding="utf-8")

    assert "source.source_system = 'TEST'" in migration
    assert "TEST_SOURCE_RECORD_IN_PRODUCTION" in migration
    assert "INSERT INTO data_quality_issues" in migration
    assert "source.source_system <> 'TEST'" not in migration
    assert "CREATE OR REPLACE FUNCTION procintel_act_is_analytics_eligible" in migration
