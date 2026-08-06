"""Curated Greek INSPIRE layers used by the product map.

The Greek catalog currently advertises matching WFS download services, but
those services expose an empty FeatureTypeList and fail their INSPIRE stored
query. The WMS view services below are live-validated and rendered through a
same-origin API proxy; they are never represented as locally ingested vector
geometry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.source_clients.raw_store import RawStore

from .capabilities import CapabilityCheckResult, validate_ogc_service


@dataclass(frozen=True)
class SelectedInspireLayer:
    layer_id: str
    catalog_dataset_id: str
    service_url: str
    wms_layer: str
    title: str
    category: str
    opacity: float
    attribution: str = "Greek Ministry of Environment and Energy (INSPIRE)"


@dataclass(frozen=True)
class SelectedLayerRefreshResult:
    layer_id: str
    wms_layer: str
    status: str
    http_status: int | None
    layer_count: int
    error: str | None


SELECTED_INSPIRE_LAYERS: tuple[SelectedInspireLayer, ...] = (
    SelectedInspireLayer(
        layer_id="flood-hazard-high",
        catalog_dataset_id="selected-flood-hazard-high-wms",
        service_url="http://geoportal.ypen.gr/geoserver/fd-hhp-wms/ows",
        wms_layer="NZ.Flood",
        title="Περιοχές υψηλής επικινδυνότητας πλημμύρας",
        category="FLOOD_RISK",
        opacity=0.52,
    ),
    SelectedInspireLayer(
        layer_id="nitrate-vulnerable-zones",
        catalog_dataset_id="selected-nitrate-vulnerable-wms",
        service_url="http://geoportal.ypen.gr/geoserver/nid-wms/ows",
        wms_layer="AM.NitrateVulnerableZone",
        title="Ευπρόσβλητες ζώνες νιτρορύπανσης",
        category="ENVIRONMENTAL_ZONE",
        opacity=0.46,
    ),
    SelectedInspireLayer(
        layer_id="nationally-protected-areas",
        catalog_dataset_id="selected-nationally-protected-wms",
        service_url="http://geoportal.ypen.gr/geoserver/nature-cdda-wms/ows",
        wms_layer="PS.ProtectedSite",
        title="Εθνικά προστατευόμενες περιοχές",
        category="PROTECTED_AREA",
        opacity=0.5,
    ),
)

_BY_ID = {layer.layer_id: layer for layer in SELECTED_INSPIRE_LAYERS}


def get_selected_layer(layer_id: str) -> SelectedInspireLayer | None:
    return _BY_ID.get(layer_id)


def normalize_wms_bbox(value: str) -> str:
    parts = value.split(",")
    if len(parts) != 4:
        raise ValueError("bbox must contain four comma-separated coordinates")
    try:
        coordinates = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise ValueError("bbox coordinates must be numeric") from exc
    if not all(isfinite(item) and abs(item) <= 50_000_000 for item in coordinates):
        raise ValueError("bbox coordinates are outside the supported range")
    if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
        raise ValueError("bbox minimum coordinates must precede maximum coordinates")
    return ",".join(format(item, ".12g") for item in coordinates)


def wms_get_map_params(
    layer: SelectedInspireLayer,
    *,
    bbox: str,
    width: int,
    height: int,
    srs: str,
) -> dict[str, str | int]:
    if srs not in {"EPSG:3857", "EPSG:4326"}:
        raise ValueError("unsupported WMS coordinate reference system")
    return {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": layer.wms_layer,
        "STYLES": "",
        "FORMAT": "image/png",
        "TRANSPARENT": "true",
        "SRS": srs,
        "BBOX": normalize_wms_bbox(bbox),
        "WIDTH": width,
        "HEIGHT": height,
    }


async def refresh_selected_inspire_layers(
    conn: AsyncConnection,
    *,
    http_client: httpx.AsyncClient,
    raw_store: RawStore,
    blocked_retry_after: timedelta,
) -> tuple[SelectedLayerRefreshResult, ...]:
    results: list[SelectedLayerRefreshResult] = []
    for layer in SELECTED_INSPIRE_LAYERS:
        result: CapabilityCheckResult = await validate_ogc_service(
            conn,
            http_client=http_client,
            raw_store=raw_store,
            service_url=layer.service_url,
            service_type="WMS",
            catalog_source="GREEK_INSPIRE_CSW",
            catalog_dataset_id=layer.catalog_dataset_id,
            title=layer.title,
            publisher="Greek Ministry of Environment and Energy",
            license_code="NO_CONDITIONS_APPLY",
            config={
                "selected_for_product": True,
                "required_layer": layer.wms_layer,
                "layer_id": layer.layer_id,
                "category": layer.category,
                "opacity": layer.opacity,
                "attribution": layer.attribution,
                "coverage": "LIVE_WMS_VIEW",
                "completeness_claim": "REMOTE_RENDERING_ONLY",
                "authoritative": True,
                "cadastral_parcels_in_scope": False,
            },
            blocked_retry_after=blocked_retry_after,
        )
        results.append(
            SelectedLayerRefreshResult(
                layer_id=layer.layer_id,
                wms_layer=layer.wms_layer,
                status=result.status,
                http_status=result.http_status,
                layer_count=result.layer_count,
                error=result.error,
            )
        )
    return tuple(results)
