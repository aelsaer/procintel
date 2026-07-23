from decimal import Decimal
from pathlib import Path

from services.ingestion.connectors.ckan.normalize import normalize_metric_csv, normalize_population_csv

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "population_sample.csv"


def test_normalizes_population_csv_and_skips_unusable_rows():
    csv_bytes = FIXTURE_PATH.read_bytes()
    rows = normalize_population_csv(csv_bytes)

    # 4 data rows in the fixture; row 3 (no geography code) and row 4 (no
    # value) must be skipped, leaving exactly 2 usable rows.
    assert len(rows) == 2

    by_code = {row.municipality_code: row for row in rows}
    assert by_code["6101"].value == Decimal("643452")
    assert by_code["6101"].nuts_code is None
    assert by_code["6102"].value == Decimal("61308")


def test_normalize_empty_csv_returns_no_rows():
    rows = normalize_population_csv(b"kallikratis_code,population\n")
    assert rows == []


def test_normalize_handles_thousands_separator():
    rows = normalize_population_csv(b"kallikratis_code,population\n6999,\"1,234\"\n")
    assert len(rows) == 1
    assert rows[0].value == Decimal("1234")


def test_normalize_metric_csv_uses_custom_value_field():
    csv_bytes = b"nuts_code,gdpPerCapita\nEL301,25000.50\n"
    rows = normalize_metric_csv(csv_bytes, value_field_candidates=("gdpPerCapita",))
    assert len(rows) == 1
    assert rows[0].nuts_code == "EL301"
    assert rows[0].value == Decimal("25000.50")


def test_normalize_metric_csv_ignores_columns_not_in_candidates():
    csv_bytes = b"nuts_code,population,gdpPerCapita\nEL301,600000,25000.50\n"
    rows = normalize_metric_csv(csv_bytes, value_field_candidates=("gdpPerCapita",))
    assert rows[0].value == Decimal("25000.50")  # not the population column
