"""Administrative-boundaries adapter (§22.2's first dataset:
"διοικητικά όρια") — parses a GeoJSON FeatureCollection resource into
`administrative_boundaries` rows.

Property names below are a best-effort guess — no sample administrative-
boundaries dataset from data.gov.gr was available at build time; fix here
once one is confirmed (docs/source-contracts/ckan-datagov.md). GeoJSON is
assumed as the resource format (data.gov.gr's own announcement lists CSV/
JSON/XML/XLSX bulk downloads; GeoJSON is standard JSON, the natural choice
for geospatial data, and needs no Shapefile-parsing dependency) — confirm
against the live dataset before relying on this.

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
                code=_first(properties, "kallikratis_code", "code", "boundary_code"),
                name=_first(properties, "name", "boundary_name"),
                nuts_code=_first(properties, "nuts_code", "nuts3"),
                wkt=wkt,
            )
        )
    return boundaries
