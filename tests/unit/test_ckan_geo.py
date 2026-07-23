import pytest

from services.ingestion.connectors.ckan.geo import geojson_to_multipolygon_wkt

SQUARE_A = [[23.7, 37.9], [23.8, 37.9], [23.8, 38.0], [23.7, 38.0], [23.7, 37.9]]
SQUARE_B = [[24.0, 38.0], [24.1, 38.0], [24.1, 38.1], [24.0, 38.1], [24.0, 38.0]]


def test_polygon_wrapped_as_single_member_multipolygon():
    wkt = geojson_to_multipolygon_wkt({"type": "Polygon", "coordinates": [SQUARE_A]})
    assert wkt == "MULTIPOLYGON(((23.7 37.9, 23.8 37.9, 23.8 38.0, 23.7 38.0, 23.7 37.9)))"


def test_multipolygon_with_two_members():
    wkt = geojson_to_multipolygon_wkt({"type": "MultiPolygon", "coordinates": [[SQUARE_A], [SQUARE_B]]})
    assert wkt.startswith("MULTIPOLYGON(")
    assert wkt.count("((") == 2


def test_polygon_with_hole_ring_included():
    hole = [[23.72, 37.92], [23.74, 37.92], [23.74, 37.94], [23.72, 37.94], [23.72, 37.92]]
    wkt = geojson_to_multipolygon_wkt({"type": "Polygon", "coordinates": [SQUARE_A, hole]})
    assert wkt == (
        "MULTIPOLYGON(((23.7 37.9, 23.8 37.9, 23.8 38.0, 23.7 38.0, 23.7 37.9), "
        "(23.72 37.92, 23.74 37.92, 23.74 37.94, 23.72 37.94, 23.72 37.92)))"
    )


def test_unsupported_geometry_type_raises():
    with pytest.raises(ValueError):
        geojson_to_multipolygon_wkt({"type": "Point", "coordinates": [23.7, 37.9]})


def test_missing_coordinates_raises():
    with pytest.raises(ValueError):
        geojson_to_multipolygon_wkt({"type": "Polygon"})
