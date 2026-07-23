"""GeoJSON geometry -> WKT MultiPolygon conversion.

`administrative_boundaries.geom` is typed `geometry(MultiPolygon, 4326)`
(db/migrations/06_reference_geo.sql) — `NOT NULL`, and specifically
MultiPolygon, not "any geometry". A single-`Polygon` GeoJSON feature is
wrapped as a one-member MultiPolygon so it satisfies that column type
without needing a second code path.

No Shapely dependency: GeoJSON's `Polygon`/`MultiPolygon` coordinate
arrays translate to WKT mechanically (nested rings of `[lon, lat]` pairs),
so a small hand-rolled formatter is enough — pulling in a full geometry
library for this one conversion isn't worth it.
"""

from __future__ import annotations

from typing import Any


def _ring_to_wkt(ring: list[list[float]]) -> str:
    points = ", ".join(f"{lon} {lat}" for lon, lat in ring)
    return f"({points})"


def _polygon_coords_to_wkt(rings: list[list[list[float]]]) -> str:
    return "(" + ", ".join(_ring_to_wkt(ring) for ring in rings) + ")"


def geojson_to_multipolygon_wkt(geometry: dict[str, Any]) -> str:
    """Converts a GeoJSON `Polygon` or `MultiPolygon` geometry object into
    a `MULTIPOLYGON(...)` WKT string. Raises `ValueError` for any other
    GeoJSON geometry type (Point/LineString/etc. aren't valid
    administrative boundaries)."""
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not geom_type or coordinates is None:
        raise ValueError(f"not a usable GeoJSON geometry: {geometry!r}")

    if geom_type == "Polygon":
        polygons_wkt = [_polygon_coords_to_wkt(coordinates)]
    elif geom_type == "MultiPolygon":
        polygons_wkt = [_polygon_coords_to_wkt(polygon_coords) for polygon_coords in coordinates]
    else:
        raise ValueError(f"unsupported GeoJSON geometry type for a boundary: {geom_type!r}")

    return "MULTIPOLYGON(" + ", ".join(polygons_wkt) + ")"
