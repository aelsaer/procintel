"""Administrative-boundaries adapter (§22.2's first dataset:
"διοικητικά όρια") — parses a GeoJSON FeatureCollection resource into
`administrative_boundaries` rows.

The default national municipal-unit layer was live-validated against
data.gov.gr. It uses ``kalcode``/``lektiko`` properties and EPSG:2100;
other standard aliases remain supported for operator-onboarded datasets.

Cadastral parcels are explicitly out of scope (§3.7) — this adapter is for
municipal/regional boundaries, postal codes, and similar area layers only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .geo import geojson_to_multipolygon_wkt


@dataclass(frozen=True)
class NormalizedBoundary:
    code: str | None
    name: str | None
    nuts_code: str | None
    wkt: str
    source_srid: int = 4326


def _first(properties: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = properties.get(key)
        if value:
            return str(value)
    return None


def normalize_boundaries_geojson(geojson_bytes: bytes) -> list[NormalizedBoundary]:
    """Rows with no usable geometry are skipped, not raised — one
    malformed feature in a large boundaries file shouldn't block ingesting
    the rest of it, same discipline as `ckan/normalize.py`'s population
    parser."""
    document = json.loads(geojson_bytes.decode("utf-8"))
    features = document.get("features", [])
    crs_name = str(
        ((document.get("crs") or {}).get("properties") or {}).get("name") or ""
    )
    source_srid = 2100 if "2100" in crs_name else 4326

    boundaries: list[NormalizedBoundary] = []
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            wkt = geojson_to_multipolygon_wkt(geometry)
        except ValueError:
            continue

        properties = feature.get("properties") or {}
        boundaries.append(
            NormalizedBoundary(
                code=_first(
                    properties,
                    "kalcode",
                    "kallikratis_code",
                    "code",
                    "boundary_code",
                ),
                name=_first(properties, "lektiko", "name", "boundary_name"),
                nuts_code=_first(properties, "nuts_code", "nuts3"),
                wkt=wkt,
                source_srid=source_srid,
            )
        )
    return boundaries
