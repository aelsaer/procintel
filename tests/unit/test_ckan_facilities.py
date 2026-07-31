from decimal import Decimal

from services.ingestion.connectors.ckan.facilities import normalize_facilities_csv


def test_normalizes_facility_rows_with_capacity_and_coordinates():
    csv_bytes = (
        b"code,name,lat,lon,students\n"
        b"SCH-001,1o Dimotiko Sxoleio,37.98,23.72,320\n"
    )
    rows = normalize_facilities_csv(csv_bytes, capacity_field_candidates=("students",))
    assert len(rows) == 1
    row = rows[0]
    assert row.code == "SCH-001"
    assert row.capacity_value == Decimal("320")
    assert row.latitude == Decimal("37.98")
    assert row.longitude == Decimal("23.72")


def test_facility_without_coordinates_or_capacity_still_kept():
    csv_bytes = b"code,name\nSCH-002,2o Dimotiko Sxoleio\n"
    rows = normalize_facilities_csv(csv_bytes)
    assert len(rows) == 1
    assert rows[0].capacity_value is None
    assert rows[0].latitude is None


def test_row_with_neither_code_nor_name_is_skipped():
    csv_bytes = b"code,name,students\n,,100\n"
    rows = normalize_facilities_csv(csv_bytes)
    assert rows == []


def test_live_minedu_school_schema_sums_registered_students():
    csv_bytes = (
        "year,jurisdiction,district,school_class,school_type,school_name,"
        "registered_students_boys,registered_students_girls\n"
        "2019,Secretariat,District,Gymnasium,Type,School of Patmos,20,3\n"
    ).encode()

    rows = normalize_facilities_csv(csv_bytes)

    assert len(rows) == 1
    assert rows[0].name == "School of Patmos"
    assert rows[0].capacity_value == Decimal("23")
