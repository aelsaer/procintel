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


def test_nonproduction_sources_are_excluded_by_the_shared_quality_gate() -> None:
    migration = (
        Path(__file__).parents[2]
        / "db"
        / "migrations"
        / "47_nonproduction_source_quality_gate.sql"
    ).read_text(encoding="utf-8")

    assert "source.source_system <> 'TEST'" in migration
    assert "CREATE OR REPLACE FUNCTION procintel_act_is_analytics_eligible" in migration
