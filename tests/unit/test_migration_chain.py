from pathlib import Path


MIGRATION_ROOT = Path(__file__).resolve().parents[2] / "db" / "migrations"


def test_migration_numbers_are_unique_contiguous_and_ordered():
    files = sorted(MIGRATION_ROOT.glob("*.sql"))
    numbers = [int(path.name.split("_", 1)[0]) for path in files]

    assert numbers == list(range(1, max(numbers) + 1))
    assert len(numbers) == len(set(numbers))


def test_migrations_are_compatible_with_the_atomic_runner():
    for path in MIGRATION_ROOT.glob("*.sql"):
        sql = path.read_text(encoding="utf-8").upper()
        assert "CREATE INDEX CONCURRENTLY" not in sql, path.name
        assert "DROP INDEX CONCURRENTLY" not in sql, path.name
