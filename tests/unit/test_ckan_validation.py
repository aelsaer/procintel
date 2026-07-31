import json

from services.ingestion.connectors.ckan.validation import validate_resource


def test_boundary_geojson_schema_is_accepted_and_fingerprinted():
    content = json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"kalcode": "1234", "lektiko": "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[23.7, 37.9], [23.8, 37.9], [23.8, 38.0], [23.7, 37.9]]],
            },
        }],
    }).encode()

    first = validate_resource(content, adapter_name="boundaries")
    second = validate_resource(content, adapter_name="boundaries")

    assert first.status == "VALID"
    assert first.detected_format == "GEOJSON"
    assert first.schema_fingerprint == second.schema_fingerprint
    assert first.errors == []


def test_boundary_schema_rejects_missing_code_name_and_polygon():
    content = json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"other": "value"},
            "geometry": {"type": "Point", "coordinates": [23.7, 37.9]},
        }],
    }).encode()

    validation = validate_resource(content, adapter_name="boundaries")

    assert validation.status == "INVALID"
    assert "boundary resource has no Polygon/MultiPolygon geometry" in validation.errors
    assert "no supported boundary code column" in validation.errors
    assert "no supported boundary name column" in validation.errors


def test_tabular_adapter_requires_a_csv_header():
    validation = validate_resource(b"", adapter_name="population")

    assert validation.detected_format == "CSV"
    assert validation.status == "INVALID"
    assert validation.errors == ["CSV resource has no header"]


def test_tabular_adapter_enforces_configured_live_columns():
    content = b"school_name,registered_students_boys,registered_students_girls\nA,20,3\n"

    validation = validate_resource(
        content,
        adapter_name="facilities",
        required_column_groups=(
            ("name", "school_name"),
            ("students", "registered_students_boys", "registered_students_girls"),
        ),
    )

    assert validation.status == "VALID"


def test_tabular_adapter_rejects_schema_drift_before_ingest():
    validation = validate_resource(
        b"unexpected,total\nA,23\n",
        adapter_name="facilities",
        required_column_groups=(("name", "school_name"),),
    )

    assert validation.status == "INVALID"
    assert validation.errors == ["none of the required columns are present: name, school_name"]
