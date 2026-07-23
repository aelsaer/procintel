from pathlib import Path

from services.ingestion.connectors.ckan.boundaries import normalize_boundaries_geojson

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "boundaries_sample.geojson"


def test_normalizes_features_and_skips_null_geometry():
    geojson_bytes = FIXTURE_PATH.read_bytes()
    boundaries = normalize_boundaries_geojson(geojson_bytes)

    # 3 features in the fixture; the one with geometry=null must be skipped
    assert len(boundaries) == 2

    by_code = {b.code: b for b in boundaries}
    assert by_code["6101"].name == "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ"
    assert by_code["6101"].nuts_code == "EL301"
    assert by_code["6101"].wkt.startswith("MULTIPOLYGON(")

    assert by_code["6102"].nuts_code is None
    assert by_code["6102"].wkt.startswith("MULTIPOLYGON(")


def test_empty_feature_collection_returns_no_rows():
    boundaries = normalize_boundaries_geojson(b'{"type": "FeatureCollection", "features": []}')
    assert boundaries == []


def test_unsupported_geometry_type_is_skipped_not_raised():
    geojson_bytes = (
        b'{"type": "FeatureCollection", "features": ['
        b'{"type": "Feature", "properties": {"code": "X"}, '
        b'"geometry": {"type": "Point", "coordinates": [1, 2]}}]}'
    )
    boundaries = normalize_boundaries_geojson(geojson_bytes)
    assert boundaries == []
