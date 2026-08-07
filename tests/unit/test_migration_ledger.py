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
