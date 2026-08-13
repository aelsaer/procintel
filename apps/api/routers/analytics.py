"""Public aggregate analytics endpoints.

These expose only public procurement aggregates. Tenant-relative opportunity
scores remain tenant-scoped data and are computed by services.analytics.cli.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date, datetime
from decimal import Decimal

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection
from services.data_quality.completeness import collect_source_completeness
from services.ingestion.connectors.inspire.selected_layers import (
    SELECTED_INSPIRE_LAYERS,
    get_selected_layer,
    wms_get_map_params,
)
from services.search_index.lexical import query_concept_pattern

from ..db import get_conn, get_engine
from ..deps import get_http_client

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])

_data_coverage_cache: tuple[float, "DataCoverageResponse"] | None = None
_data_coverage_lock = asyncio.Lock()
_data_coverage_refresh_task: asyncio.Task[None] | None = None


class TopSupplierResponse(BaseModel):
    supplier_id: str
    supplier_name: str
    afm: str | None = None
    recorded_value: Decimal | None = None
    act_count: int


class TopBuyerResponse(BaseModel):
    buyer_id: str
    buyer_name: str
    vat: str | None = None
    recorded_value: Decimal | None = None
    act_count: int
    supplier_count: int


class MarketOverviewResponse(BaseModel):
    process_count: int
    act_count: int
    opportunity_count: int
    contract_count: int
    notice_count: int
    payment_count: int
    recorded_contract_value: Decimal | None = None
    acts_with_geo: int
    acts_with_precise_geo: int


class RegionAnalyticsResponse(BaseModel):
    nuts_code: str
    region_name: str
    act_count: int
    opportunity_count: int
    notice_count: int
    contract_count: int
    recorded_contract_value: Decimal | None = None


class OpportunityResponse(BaseModel):
    act_id: str
    process_id: str | None = None
    title: str | None = None
    act_type: str
    buyer_name: str | None = None
    amount_gross: Decimal | None = None
    event_date: date | None = None
    submission_date: date | None = None
    cpv_codes: list[str]
    nuts_codes: list[str]
    location_labels: list[str]
    fit_score: float
    evidence: list[str]


class RegionActivityResponse(BaseModel):
    act_id: str
    process_id: str | None = None
    title: str | None = None
    act_type: str
    status: str | None = None
    buyer_name: str | None = None
    amount_gross: Decimal | None = None
    event_date: date | None = None
    cpv_codes: list[str]
    nuts_codes: list[str]
    location_labels: list[str]


class GeocodedLocationAnalyticsResponse(BaseModel):
    label: str
    nuts_code: str | None = None
    municipality_name: str | None = None
    regional_unit_name: str | None = None
    region_name: str | None = None
    latitude: float
    longitude: float
    act_count: int
    opportunity_count: int
    contract_count: int
    recorded_contract_value: Decimal | None = None
    minimum_confidence: float | None = None


class SourceResourceCoverage(BaseModel):
    resource_type: str
    record_count: int
    parsed_count: int
    failed_count: int


class SourceCoverage(BaseModel):
    source_system: str
    record_count: int
    parsed_count: int
    failed_count: int
    latest_fetched_at: datetime | None = None
    resources: list[SourceResourceCoverage]


class DataConnectionCoverage(BaseModel):
    source: str
    target: str
    relation: str
    available_records: int
    linked_records: int
    status: str


class ConnectorRunCoverage(BaseModel):
    source_system: str
    resource_type: str
    partition_key: str
    status: str
    records_fetched: int
    records_upserted: int
    records_unchanged: int = 0
    records_failed: int = 0
    enrichment_succeeded: int = 0
    enrichment_failed: int = 0
    enrichment_deferred: int = 0
    started_at: datetime
    finished_at: datetime | None = None
    error: dict | None = None


class DatasetValidationCoverage(BaseModel):
    dataset_name: str
    adapter_name: str
    resource_url: str
    detected_format: str | None = None
    columns: list[str]
    status: str
    errors: list
    validated_at: datetime


class SourceCompletenessResponse(BaseModel):
    source_system: str
    status: str
    score: float
    claim_level: str
    expected_basis: str
    observed_records: int
    expected_records: int | None = None
    canonical_records: int
    records_with_documents: int
    records_with_parties: int
    records_with_locations: int
    failed_records: int
    pending_enrichments: int
    freshness_seconds: int | None = None
    freshness_target_seconds: int
    dimensions: dict[str, float | None]
    findings: list[dict]


class DataCoverageResponse(BaseModel):
    generated_at: datetime
    totals: dict[str, int]
    sources: list[SourceCoverage]
    connections: list[DataConnectionCoverage]
    recent_runs: list[ConnectorRunCoverage]
    dataset_validations: list[DatasetValidationCoverage]
    assessments: list[SourceCompletenessResponse]


class SpatialReferenceServiceResponse(BaseModel):
    catalog_source: str
    service_url: str
    service_type: str
    service_version: str | None = None
    title: str | None = None
    provider_name: str | None = None
    license_code: str | None = None
    fees: str | None = None
    formats: list[str]
    layer_count: int
    status: str
    http_status: int | None = None
    checked_at: datetime
    last_available_at: datetime | None = None
    last_error: dict | None = None
    coverage: str | None = None
    catalog_modified: str | None = None
    authoritative: bool = False
    cadastral_parcels_in_scope: bool = False


class CatalogReferenceDatasetResponse(BaseModel):
    catalog_dataset_id: str
    title: str
    publisher: str | None = None
    license_code: str | None = None
    resource_type: str | None = None
    resource_url: str | None = None
    ingestion_status: str
    domain: str | None = None
    provenance_role: str | None = None
    geographic_scope: str | None = None
    temporal_scope: str | None = None
    completeness_claim: str | None = None
    primary_source: bool = False
    last_seen_at: datetime | None = None


class ReferenceSourcesResponse(BaseModel):
    spatial_services: list[SpatialReferenceServiceResponse]
    catalog_datasets: list[CatalogReferenceDatasetResponse]


class ReferenceMapLayerResponse(BaseModel):
    layer_id: str
    title: str
    category: str
    opacity: float
    attribution: str
    status: str
    checked_at: datetime | None = None


GREEK_NUTS_2_NAMES = {
    "EL30": "Αττική",
    "EL41": "Βόρειο Αιγαίο",
    "EL42": "Νότιο Αιγαίο",
    "EL43": "Κρήτη",
    "EL51": "Ανατολική Μακεδονία και Θράκη",
    "EL52": "Κεντρική Μακεδονία",
    "EL53": "Δυτική Μακεδονία",
    "EL54": "Ήπειρος",
    "EL61": "Θεσσαλία",
    "EL62": "Ιόνια Νησιά",
    "EL63": "Δυτική Ελλάδα",
    "EL64": "Στερεά Ελλάδα",
    "EL65": "Πελοπόννησος",
}


def _cpv_likes(cpv_prefix: str | None, cpv_prefixes: str | None) -> list[str]:
    values = [
        value.strip().split("-", 1)[0]
        for value in [cpv_prefix or "", *(cpv_prefixes or "").split(",")]
        if value.strip()
    ]
    return [f"{value}%" for value in dict.fromkeys(values)]


def _market_filter_params(
    *,
    cpv_prefix: str | None,
    cpv_prefixes: str | None,
    keyword: str | None,
    keywords: str | None = None,
    taxonomy_match: str | None = None,
) -> dict[str, object]:
    cpv_likes = _cpv_likes(cpv_prefix, cpv_prefixes)
    keyword_values = [
        value.strip()
        for value in [keyword or "", *(keywords or "").split(",")]
        if value.strip()
    ]
    keyword_patterns = [
        pattern
        for value in dict.fromkeys(keyword_values)
        if (pattern := query_concept_pattern(value)) is not None
    ]
    match_mode = str(taxonomy_match or "ANY").upper()
    if match_mode == "KEYWORD_REQUIRED" and keyword_patterns:
        cpv_likes = []
    match_all = match_mode in {"ALL", "CPV_AND_KEYWORD"}
    return {
        "has_taxonomy_filter": bool(cpv_likes or keyword_patterns),
        "has_cpv_filter": bool(cpv_likes),
        "has_keyword_filter": bool(keyword_patterns),
        "taxonomy_match_mode": match_mode,
        "taxonomy_match_all": match_all,
        "cpv_likes": cpv_likes,
        "keyword_patterns": keyword_patterns,
    }


def _score_opportunity(
    *,
    act_type: str,
    amount_gross: Decimal | None,
    submission_date: date | None,
    cpv_codes: list[str],
    nuts_codes: list[str],
    cpv_prefix: str | None,
    nuts_code: str | None,
    amount_min: Decimal | None,
) -> tuple[float, list[str]]:
    score = 25.0
    evidence: list[str] = []

    if cpv_prefix and any(code.startswith(cpv_prefix) for code in cpv_codes):
        score += 30
        evidence.append(f"CPV {cpv_prefix}")

    normalized_nuts = nuts_code.upper() if nuts_code else None
    if normalized_nuts and any(code.upper().startswith(normalized_nuts) for code in nuts_codes):
        score += 20
        evidence.append(normalized_nuts)

    if amount_min is not None and amount_gross is not None and amount_gross >= amount_min:
        score += 10
        evidence.append("value fit")

    if act_type == "NOTICE":
        score += 10
        evidence.append("notice")
    elif act_type in {"REQUEST", "APPROVED_REQUEST"}:
        score += 5
        evidence.append("early signal")

    if submission_date is not None and submission_date >= date.today():
        score += 5
        evidence.append("open deadline")

    return min(score, 100.0), evidence


@router.get("/data-coverage", response_model=DataCoverageResponse)
async def data_coverage(conn: AsyncConnection = Depends(get_conn)) -> DataCoverageResponse:
    global _data_coverage_cache, _data_coverage_refresh_task

    ttl_seconds = max(5.0, float(os.environ.get("DATA_COVERAGE_CACHE_SECONDS", "30")))
    now = time.monotonic()
    if _data_coverage_cache:
        if (
            now - _data_coverage_cache[0] >= ttl_seconds
            and (_data_coverage_refresh_task is None or _data_coverage_refresh_task.done())
        ):
            _data_coverage_refresh_task = asyncio.create_task(
                _refresh_data_coverage_cache()
            )
        return _data_coverage_cache[1]

    async with _data_coverage_lock:
        now = time.monotonic()
        if _data_coverage_cache and now - _data_coverage_cache[0] < ttl_seconds:
            return _data_coverage_cache[1]
        response = await _build_data_coverage(conn)
        _data_coverage_cache = (time.monotonic(), response)
        return response


async def _refresh_data_coverage_cache() -> None:
    global _data_coverage_cache

    async with _data_coverage_lock:
        async with get_engine().connect() as conn:
            response = await _build_data_coverage(conn)
        _data_coverage_cache = (time.monotonic(), response)


async def _build_data_coverage(conn: AsyncConnection) -> DataCoverageResponse:
    """Live ingestion, canonicalization and cross-source linkage coverage."""
    resource_rows = (await conn.execute(sa.text(
        """
        SELECT source_system, resource_type,
               COUNT(*) AS record_count,
               COUNT(*) FILTER (WHERE parse_status='PARSED') AS parsed_count,
               COUNT(*) FILTER (WHERE parse_status='FAILED') AS failed_count,
               MAX(fetched_at) AS latest_fetched_at
        FROM source_records
        GROUP BY source_system, resource_type
        ORDER BY source_system, resource_type
        """
    ))).mappings().all()

    grouped: dict[str, dict] = {}
    for row in resource_rows:
        source = grouped.setdefault(row["source_system"], {
            "source_system": row["source_system"], "record_count": 0,
            "parsed_count": 0, "failed_count": 0,
            "latest_fetched_at": None, "resources": [],
        })
        source["record_count"] += row["record_count"]
        source["parsed_count"] += row["parsed_count"]
        source["failed_count"] += row["failed_count"]
        if source["latest_fetched_at"] is None or row["latest_fetched_at"] > source["latest_fetched_at"]:
            source["latest_fetched_at"] = row["latest_fetched_at"]
        source["resources"].append(SourceResourceCoverage(
            resource_type=row["resource_type"], record_count=row["record_count"],
            parsed_count=row["parsed_count"], failed_count=row["failed_count"],
        ))
    sources = [SourceCoverage(**value) for value in grouped.values()]
    source_counts = {source.source_system: source.record_count for source in sources}

    totals_row = (await conn.execute(sa.text(
        """
        SELECT
          (SELECT COUNT(*) FROM source_records) AS source_records,
          (SELECT COUNT(*) FROM procurement_processes WHERE record_status='ACTIVE') AS processes,
          (SELECT COUNT(*) FROM procurement_acts WHERE is_current=TRUE) AS acts,
          (SELECT COUNT(*) FROM entities WHERE status='ACTIVE') AS entities,
          (SELECT COUNT(*) FROM act_links) AS act_links,
          (SELECT COUNT(*) FROM documents) AS documents,
          (SELECT COUNT(*) FROM field_provenance) AS field_references,
          (SELECT COUNT(*) FROM external_dataset_validations) AS dataset_validations,
          (SELECT COUNT(*) FROM act_locations WHERE geom IS NOT NULL) AS precise_locations,
          (SELECT COUNT(*) FROM opportunity_scores) AS opportunity_scores
          ,(SELECT COUNT(*) FROM enrichment_jobs
              WHERE status IN ('QUEUED', 'FAILED', 'BLOCKED_CONFIG')) AS pending_enrichments
          ,(SELECT COUNT(*) FROM enrichment_jobs
              WHERE status='BLOCKED_CONFIG') AS blocked_enrichments
          ,(SELECT COUNT(*) FROM data_quality_issues
              WHERE status IN ('OPEN', 'ACKNOWLEDGED')) AS open_data_quality_issues
          ,(SELECT COUNT(*) FROM nuts_areas
              WHERE classification_version='NUTS-2024') AS nuts_areas
          ,(SELECT COUNT(DISTINCT postal_code) FROM postal_code_nuts
              WHERE country_code='GR'
                AND classification_version='NUTS-2024') AS postal_codes
        """
    ))).mappings().one()
    totals = {key: int(value or 0) for key, value in totals_row.items()}

    links = (await conn.execute(sa.text(
        """
        SELECT
          (SELECT COUNT(*) FROM procurement_acts a JOIN source_records sr ON sr.id=a.source_record_id
             WHERE a.is_current=TRUE AND sr.source_system='KHMDHS') AS khmdhs_acts,
          (SELECT COUNT(*) FROM process_members) AS lifecycle_members,
          (SELECT COUNT(*) FROM act_links WHERE link_type='APPROVES') AS diavgeia_links,
          (SELECT COUNT(DISTINCT snapshot.entity_id) FROM entity_company_snapshots snapshot
             WHERE EXISTS (SELECT 1 FROM act_parties party WHERE party.entity_id=snapshot.entity_id)) AS gemi_links,
          (SELECT COUNT(*) FROM act_links WHERE link_type='PUBLISHED_AS') AS ted_links,
          (SELECT COUNT(*) FROM funding_links) AS funding_links,
          (SELECT COUNT(*) FROM mef_expenses WHERE linked_act_id IS NOT NULL) AS mef_links,
          (SELECT COUNT(*) FROM document_act_links) AS document_links,
          (SELECT COUNT(*) FROM act_locations WHERE geom IS NOT NULL) AS geocoding_links
          ,(SELECT COUNT(*) FROM administrative_boundaries) AS ckan_boundaries
          ,(SELECT COUNT(*) FROM nuts_areas
              WHERE classification_version='NUTS-2024') AS inspire_nuts
          ,(SELECT COUNT(*) FROM postal_code_nuts
              WHERE country_code='GR'
                AND classification_version='NUTS-2024') AS inspire_postal_nuts
          ,(SELECT COUNT(*) FROM spatial_service_capabilities
              WHERE catalog_source='KTIMATOLOGIO_INSPIRE'
                AND status='AVAILABLE') AS ktimatologio_services
          ,(SELECT COUNT(*) FROM spatial_service_capabilities
              WHERE catalog_source='KTIMATOLOGIO_INSPIRE') AS ktimatologio_checks
          ,(SELECT COUNT(*) FROM spatial_service_capabilities
              WHERE catalog_source='GREEK_INSPIRE_CSW'
                AND status='AVAILABLE') AS greek_inspire_services
          ,(SELECT COUNT(*) FROM spatial_service_capabilities
              WHERE catalog_source='GREEK_INSPIRE_CSW') AS greek_inspire_checks
          ,(SELECT COUNT(*) FROM external_datasets
              WHERE catalog_source='GREEK_INSPIRE_CSW'
                AND ingestion_status='ONBOARDED'
                AND COALESCE((config->>'selected_for_product')::boolean, FALSE)
           ) AS selected_inspire_map_layers
          ,(SELECT COUNT(*) FROM external_datasets
              WHERE catalog_source='DATA_GOV_GR'
                AND ingestion_status='METADATA_ONLY') AS data_gov_catalog_references
        """
    ))).mappings().one()

    def connection(source: str, target: str, relation: str, linked: int, available: int) -> DataConnectionCoverage:
        status = "CONNECTED" if linked > 0 else "LOADED_UNLINKED" if available > 0 else "NOT_LOADED"
        return DataConnectionCoverage(
            source=source, target=target, relation=relation,
            available_records=available, linked_records=linked, status=status,
        )

    connections = [
        connection("KHMDHS", "CANONICAL", "NORMALIZES", links["khmdhs_acts"], source_counts.get("KHMDHS", 0)),
        connection("KHMDHS", "LIFECYCLE", "GROUPS", links["lifecycle_members"], source_counts.get("KHMDHS", 0)),
        connection("DIAVGEIA", "KHMDHS", "APPROVES", links["diavgeia_links"], source_counts.get("DIAVGEIA", 0)),
        connection("GEMI", "SUPPLIERS", "ENRICHES", links["gemi_links"], source_counts.get("GEMI", 0)),
        connection("TED", "KHMDHS", "PUBLISHED_AS", links["ted_links"], source_counts.get("TED", 0)),
        connection("ANAPTYXI", "KHMDHS", "FUNDS", links["funding_links"], source_counts.get("ANAPTYXI", 0)),
        connection("MEF", "KHMDHS", "EXPENSE_FOR", links["mef_links"], source_counts.get("MEF", 0)),
        connection("DOCUMENTS", "ACTS", "EVIDENCES", links["document_links"], source_counts.get("DOCUMENTS", 0)),
        connection("GEOCODING", "ACTS", "LOCATES", links["geocoding_links"], totals["acts"]),
        connection("CKAN", "GEOCODING", "PROVIDES_BOUNDARIES", links["ckan_boundaries"], source_counts.get("CKAN", 0)),
        connection("INSPIRE_GISCO", "GEOCODING", "NUTS_HIERARCHY", links["inspire_nuts"], source_counts.get("INSPIRE", 0)),
        connection("INSPIRE_GISCO", "GEOCODING", "POSTAL_TO_NUTS", links["inspire_postal_nuts"], source_counts.get("INSPIRE", 0)),
        DataConnectionCoverage(
            source="KTIMATOLOGIO_INSPIRE",
            target="GEOCODING",
            relation="OGC_CAPABILITIES",
            available_records=links["ktimatologio_checks"],
            linked_records=links["ktimatologio_services"],
            status=(
                "CONNECTED"
                if links["ktimatologio_services"]
                else "BLOCKED_UPSTREAM"
                if links["ktimatologio_checks"]
                else "NOT_LOADED"
            ),
        ),
        DataConnectionCoverage(
            source="GREEK_INSPIRE_CSW",
            target="GEOCODING",
            relation="DISCOVERS_OGC_SERVICES",
            available_records=links["greek_inspire_checks"],
            linked_records=links["greek_inspire_services"],
            status=(
                "CONNECTED"
                if links["greek_inspire_services"]
                else "BLOCKED_UPSTREAM"
                if links["greek_inspire_checks"]
                else "NOT_LOADED"
            ),
        ),
        DataConnectionCoverage(
            source="GREEK_INSPIRE_WMS",
            target="ANALYTICS_MAP",
            relation="THEMATIC_MAP_OVERLAYS",
            available_records=3,
            linked_records=links["selected_inspire_map_layers"],
            status=(
                "CONNECTED"
                if links["selected_inspire_map_layers"]
                else "NOT_LOADED"
            ),
        ),
        DataConnectionCoverage(
            source="DATA_GOV_GR_CATALOG",
            target="ENRICHMENT_REGISTRY",
            relation="SECONDARY_REFERENCE_METADATA",
            available_records=links["data_gov_catalog_references"],
            linked_records=0,
            status=(
                "LOADED_UNLINKED"
                if links["data_gov_catalog_references"]
                else "NOT_LOADED"
            ),
        ),
    ]

    run_rows = (await conn.execute(sa.text(
        """
        SELECT source_system, resource_type, partition_key, status,
               records_fetched, records_upserted, records_unchanged,
               records_failed, enrichment_succeeded, enrichment_failed,
               enrichment_deferred, started_at, finished_at, error
        FROM connector_runs
        ORDER BY started_at DESC
        LIMIT 30
        """
    ))).mappings().all()
    validation_rows = (await conn.execute(sa.text(
        """
        SELECT dataset.title AS dataset_name,validation.adapter_name,
               validation.resource_url,validation.detected_format,
               validation.columns,validation.status,validation.errors,
               validation.validated_at
        FROM external_dataset_validations validation
        JOIN external_datasets dataset ON dataset.id=validation.external_dataset_id
        ORDER BY validation.validated_at DESC LIMIT 50
        """
    ))).mappings().all()
    generated_at = (await conn.execute(sa.text("SELECT now()"))).scalar_one()
    completeness = await collect_source_completeness(conn)
    return DataCoverageResponse(
        generated_at=generated_at, totals=totals, sources=sources,
        connections=connections,
        recent_runs=[ConnectorRunCoverage(**dict(row)) for row in run_rows],
        dataset_validations=[
            DatasetValidationCoverage(**dict(row)) for row in validation_rows
        ],
        assessments=[
            SourceCompletenessResponse(
                **{
                    **assessment.__dict__,
                    "findings": list(assessment.findings),
                }
            )
            for assessment in completeness
        ],
    )


@router.get("/reference-sources", response_model=ReferenceSourcesResponse)
async def reference_sources(
    status: str | None = Query(default=None, max_length=32),
    conn: AsyncConnection = Depends(get_conn),
) -> ReferenceSourcesResponse:
    spatial_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT catalog_source, service_url, service_type, service_version,
                       title, provider_name, access_constraints AS license_code,
                       fees, formats, jsonb_array_length(layers) AS layer_count,
                       capability.status, capability.http_status,
                       capability.checked_at, capability.last_available_at,
                       capability.last_error,
                       dataset.config->>'coverage' AS coverage,
                       dataset.config->>'catalog_modified' AS catalog_modified,
                       COALESCE((dataset.config->>'authoritative')::boolean, FALSE) AS authoritative,
                       COALESCE((dataset.config->>'cadastral_parcels_in_scope')::boolean, FALSE)
                           AS cadastral_parcels_in_scope
                FROM spatial_service_capabilities capability
                LEFT JOIN LATERAL (
                    SELECT external.config
                    FROM external_datasets external
                    WHERE external.resource_url = capability.service_url
                      AND external.resource_type = capability.service_type
                    ORDER BY external.last_seen_at DESC NULLS LAST
                    LIMIT 1
                ) dataset ON TRUE
                WHERE CAST(:status AS TEXT) IS NULL
                   OR capability.status = UPPER(CAST(:status AS TEXT))
                ORDER BY
                    CASE capability.status WHEN 'AVAILABLE' THEN 0 WHEN 'DEGRADED' THEN 1 ELSE 2 END,
                    capability.checked_at DESC,
                    capability.title NULLS LAST
                """
            ),
            {"status": status},
        )
    ).mappings().all()
    catalog_rows = (
        await conn.execute(
            sa.text(
                """
                SELECT catalog_dataset_id, title, publisher, license_code,
                       resource_type, resource_url, ingestion_status,
                       config->>'domain' AS domain,
                       config->>'provenance_role' AS provenance_role,
                       config->>'geographic_scope' AS geographic_scope,
                       config->>'temporal_scope' AS temporal_scope,
                       config->>'completeness_claim' AS completeness_claim,
                       COALESCE((config->>'primary_source')::boolean, FALSE) AS primary_source,
                       last_seen_at
                FROM external_datasets
                WHERE catalog_source = 'DATA_GOV_GR'
                  AND ingestion_status = 'METADATA_ONLY'
                ORDER BY domain, title
                """
            )
        )
    ).mappings().all()
    return ReferenceSourcesResponse(
        spatial_services=[
            SpatialReferenceServiceResponse(**dict(row)) for row in spatial_rows
        ],
        catalog_datasets=[
            CatalogReferenceDatasetResponse(**dict(row)) for row in catalog_rows
        ],
    )


@router.get("/reference-map-layers", response_model=list[ReferenceMapLayerResponse])
async def reference_map_layers(
    conn: AsyncConnection = Depends(get_conn),
) -> list[ReferenceMapLayerResponse]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT service_url, status, checked_at
                FROM spatial_service_capabilities
                WHERE service_type = 'WMS'
                  AND service_url = ANY(CAST(:service_urls AS TEXT[]))
                """
            ),
            {"service_urls": [layer.service_url for layer in SELECTED_INSPIRE_LAYERS]},
        )
    ).mappings().all()
    health = {row["service_url"]: row for row in rows}
    return [
        ReferenceMapLayerResponse(
            layer_id=layer.layer_id,
            title=layer.title,
            category=layer.category,
            opacity=layer.opacity,
            attribution=layer.attribution,
            status=(health.get(layer.service_url) or {}).get("status", "NOT_CHECKED"),
            checked_at=(health.get(layer.service_url) or {}).get("checked_at"),
        )
        for layer in SELECTED_INSPIRE_LAYERS
    ]


@router.get("/reference-map/{layer_id}", response_class=Response)
async def reference_map_tile(
    layer_id: str,
    bbox: str = Query(min_length=7, max_length=160),
    width: int = Query(default=256, ge=1, le=1024),
    height: int = Query(default=256, ge=1, le=1024),
    srs: str = Query(default="EPSG:3857", max_length=16),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    layer = get_selected_layer(layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail="Unknown reference map layer")
    try:
        params = wms_get_map_params(
            layer,
            bbox=bbox,
            width=width,
            height=height,
            srs=srs.upper(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        upstream = await http_client.get(layer.service_url, params=params)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"INSPIRE WMS unavailable: {type(exc).__name__}",
        ) from exc
    content_type = upstream.headers.get("content-type", "").split(";", 1)[0]
    if content_type != "image/png":
        raise HTTPException(status_code=502, detail="INSPIRE WMS returned a non-image response")
    return Response(
        content=upstream.content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600, stale-if-error=86400",
            "X-Content-Source": "Greek INSPIRE WMS",
        },
    )


@router.get("/top-suppliers", response_model=list[TopSupplierResponse])
async def top_suppliers(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cpv_prefix: str | None = Query(default=None),
    cpv_prefixes: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    nuts_code: str | None = Query(default=None),
    municipality: str | None = Query(default=None),
    limit: int = Query(default=20, gt=0, le=100),
    conn: AsyncConnection = Depends(get_conn),
) -> list[TopSupplierResponse]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH supplier_acts AS (
                    SELECT DISTINCT
                        a.id AS act_id,
                        ap.entity_id AS supplier_id,
                        COALESCE(ap.amount, a.amount_gross, a.amount_net) AS amount
                    FROM procurement_acts a
                    JOIN act_parties ap
                        ON ap.act_id = a.id
                       AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
                    WHERE a.act_type = 'CONTRACT'
                      AND a.is_current = TRUE
                      AND procintel_act_is_analytics_eligible(a.id)
                      AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) >= CAST(:date_from AS DATE))
                      AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) <= CAST(:date_to AS DATE))
                      AND procintel_taxonomy_match(
                          a.id,
                          a.title,
                          CAST(:cpv_likes AS TEXT[]),
                          CAST(:keyword_patterns AS TEXT[]),
                          CAST(:taxonomy_match_all AS BOOLEAN)
                      )
                      AND (
                          CAST(:nuts_code AS TEXT) IS NULL
                          OR EXISTS (
                              SELECT 1
                              FROM act_locations aloc
                              WHERE aloc.act_id = a.id
                                AND UPPER(aloc.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                          )
                      )
                      AND (
                          CAST(:municipality_like AS TEXT) IS NULL
                          OR EXISTS (
                              SELECT 1 FROM act_locations municipality_loc
                              WHERE municipality_loc.act_id = a.id
                                AND (
                                    municipality_loc.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                    OR municipality_loc.place_text ILIKE CAST(:municipality_like AS TEXT)
                                )
                          )
                      )
                )
                SELECT
                    sa.supplier_id,
                    e.canonical_name AS supplier_name,
                    ei.value_normalized AS afm,
                    SUM(sa.amount) AS recorded_value,
                    COUNT(DISTINCT sa.act_id) AS act_count
                FROM supplier_acts sa
                JOIN entities e ON e.id = sa.supplier_id
                LEFT JOIN entity_identifiers ei
                    ON ei.entity_id = e.id
                   AND ei.scheme = 'AFM'
                   AND ei.is_current = TRUE
                GROUP BY sa.supplier_id, e.canonical_name, ei.value_normalized
                ORDER BY SUM(sa.amount) DESC NULLS LAST, COUNT(DISTINCT sa.act_id) DESC
                LIMIT :limit
                """
            ),
            {
                "date_from": date_from,
                "date_to": date_to,
                **_market_filter_params(
                    cpv_prefix=cpv_prefix, cpv_prefixes=cpv_prefixes,
                    keyword=keyword, keywords=keywords, taxonomy_match=taxonomy_match,
                ),
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
                "limit": limit,
            },
        )
    ).all()
    return [
        TopSupplierResponse(
            supplier_id=str(row.supplier_id),
            supplier_name=row.supplier_name,
            afm=row.afm,
            recorded_value=row.recorded_value,
            act_count=row.act_count,
        )
        for row in rows
    ]


@router.get("/top-buyers", response_model=list[TopBuyerResponse])
async def top_buyers(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cpv_prefix: str | None = Query(default=None),
    cpv_prefixes: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    nuts_code: str | None = Query(default=None),
    municipality: str | None = Query(default=None),
    limit: int = Query(default=20, gt=0, le=100),
    conn: AsyncConnection = Depends(get_conn),
) -> list[TopBuyerResponse]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH buyer_acts AS (
                    SELECT DISTINCT
                        a.id AS act_id,
                        ap.entity_id AS buyer_id,
                        COALESCE(ap.amount, a.amount_gross, a.amount_net) AS amount
                    FROM procurement_acts a
                    JOIN act_parties ap
                        ON ap.act_id = a.id
                       AND ap.party_role IN ('BUYER', 'CONTRACTING_AUTHORITY')
                    WHERE a.act_type = 'CONTRACT'
                      AND a.is_current = TRUE
                      AND procintel_act_is_analytics_eligible(a.id)
                      AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) >= CAST(:date_from AS DATE))
                      AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) <= CAST(:date_to AS DATE))
                      AND procintel_taxonomy_match(
                          a.id,
                          a.title,
                          CAST(:cpv_likes AS TEXT[]),
                          CAST(:keyword_patterns AS TEXT[]),
                          CAST(:taxonomy_match_all AS BOOLEAN)
                      )
                      AND (
                          CAST(:nuts_code AS TEXT) IS NULL
                          OR EXISTS (
                              SELECT 1
                              FROM act_locations aloc
                              WHERE aloc.act_id = a.id
                                AND UPPER(aloc.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                          )
                      )
                      AND (
                          CAST(:municipality_like AS TEXT) IS NULL
                          OR EXISTS (
                              SELECT 1 FROM act_locations municipality_loc
                              WHERE municipality_loc.act_id = a.id
                                AND (
                                    municipality_loc.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                    OR municipality_loc.place_text ILIKE CAST(:municipality_like AS TEXT)
                                )
                          )
                      )
                )
                SELECT
                    ba.buyer_id,
                    e.canonical_name AS buyer_name,
                    ei.value_normalized AS vat,
                    SUM(ba.amount) AS recorded_value,
                    COUNT(DISTINCT ba.act_id) AS act_count,
                    (
                        SELECT COUNT(DISTINCT sp.entity_id)
                        FROM act_parties sp
                        WHERE sp.act_id IN (SELECT act_id FROM buyer_acts WHERE buyer_id = ba.buyer_id)
                          AND sp.party_role IN ('SUPPLIER', 'CONTRACTOR')
                    ) AS supplier_count
                FROM buyer_acts ba
                JOIN entities e ON e.id = ba.buyer_id
                LEFT JOIN entity_identifiers ei
                    ON ei.entity_id = e.id
                   AND ei.scheme = 'AFM'
                   AND ei.is_current = TRUE
                GROUP BY ba.buyer_id, e.canonical_name, ei.value_normalized
                ORDER BY SUM(ba.amount) DESC NULLS LAST, COUNT(DISTINCT ba.act_id) DESC
                LIMIT :limit
                """
            ),
            {
                "date_from": date_from,
                "date_to": date_to,
                **_market_filter_params(
                    cpv_prefix=cpv_prefix, cpv_prefixes=cpv_prefixes,
                    keyword=keyword, keywords=keywords, taxonomy_match=taxonomy_match,
                ),
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
                "limit": limit,
            },
        )
    ).all()
    return [
        TopBuyerResponse(
            buyer_id=str(row.buyer_id),
            buyer_name=row.buyer_name,
            vat=row.vat,
            recorded_value=row.recorded_value,
            act_count=row.act_count,
            supplier_count=row.supplier_count,
        )
        for row in rows
    ]


@router.get("/market-overview", response_model=MarketOverviewResponse)
async def market_overview(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cpv_prefix: str | None = Query(default=None),
    cpv_prefixes: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    nuts_code: str | None = Query(default=None),
    municipality: str | None = Query(default=None),
    conn: AsyncConnection = Depends(get_conn),
) -> MarketOverviewResponse:
    row = (
        await conn.execute(
            sa.text(
                """
                SELECT
                    COUNT(DISTINCT a.process_id) AS process_count,
                    COUNT(DISTINCT a.id) AS act_count,
                    COUNT(DISTINCT a.id) FILTER (
                        WHERE a.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')
                    ) AS opportunity_count,
                    COUNT(DISTINCT a.id) FILTER (WHERE a.act_type = 'CONTRACT') AS contract_count,
                    COUNT(DISTINCT a.id) FILTER (WHERE a.act_type = 'NOTICE') AS notice_count,
                    COUNT(DISTINCT a.id) FILTER (WHERE a.act_type = 'PAYMENT') AS payment_count,
                    SUM(COALESCE(a.amount_gross, a.amount_net))
                        FILTER (WHERE a.act_type = 'CONTRACT') AS recorded_contract_value,
                    COUNT(DISTINCT a.id) FILTER (
                        WHERE geo.has_geo
                    ) AS acts_with_geo,
                    COUNT(DISTINCT a.id) FILTER (
                        WHERE geo.has_precise_geo
                    ) AS acts_with_precise_geo
                FROM procurement_acts a
                JOIN source_records source ON source.id = a.source_record_id
                LEFT JOIN (
                    SELECT act_id, TRUE AS has_geo, BOOL_OR(geom IS NOT NULL) AS has_precise_geo
                    FROM act_locations
                    GROUP BY act_id
                ) geo ON geo.act_id = a.id
                WHERE a.is_current = TRUE
                  AND NOT (
                      source.source_system = 'KHMDHS'
                      AND source.resource_type = 'adamChain'
                      AND a.title IS NULL
                      AND a.amount_net IS NULL
                      AND a.amount_gross IS NULL
                      AND a.publication_date IS NULL
                      AND a.submission_date IS NULL
                      AND a.decision_date IS NULL
                      AND a.start_date IS NULL
                      AND a.end_date IS NULL
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM data_quality_issues issue
                      WHERE issue.object_id = a.id
                        AND LOWER(COALESCE(issue.object_type, '')) IN ('procurement_act', 'procurement_acts')
                        AND issue.severity IN ('ERROR', 'BLOCKING')
                        AND issue.status <> 'RESOLVED'
                  )
                  AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) >= CAST(:date_from AS DATE))
                  AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) <= CAST(:date_to AS DATE))
                  AND (
                      NOT CAST(:has_taxonomy_filter AS BOOLEAN)
                      OR procintel_taxonomy_match(
                          a.id,
                          a.title,
                          CAST(:cpv_likes AS TEXT[]),
                          CAST(:keyword_patterns AS TEXT[]),
                          CAST(:taxonomy_match_all AS BOOLEAN)
                      )
                  )
                  AND (
                      CAST(:nuts_code AS TEXT) IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM act_locations aloc
                          WHERE aloc.act_id = a.id
                            AND UPPER(aloc.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                      )
                  )
                  AND (
                      CAST(:municipality_like AS TEXT) IS NULL
                      OR EXISTS (
                          SELECT 1 FROM act_locations municipality_loc
                          WHERE municipality_loc.act_id = a.id
                            AND (
                                municipality_loc.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                OR municipality_loc.place_text ILIKE CAST(:municipality_like AS TEXT)
                            )
                      )
                  )
                """
            ),
            {
                "date_from": date_from,
                "date_to": date_to,
                **_market_filter_params(
                    cpv_prefix=cpv_prefix, cpv_prefixes=cpv_prefixes,
                    keyword=keyword, keywords=keywords, taxonomy_match=taxonomy_match,
                ),
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
            },
        )
    ).one()
    return MarketOverviewResponse(
        process_count=row.process_count,
        act_count=row.act_count,
        opportunity_count=row.opportunity_count,
        contract_count=row.contract_count,
        notice_count=row.notice_count,
        payment_count=row.payment_count,
        recorded_contract_value=row.recorded_contract_value,
        acts_with_geo=row.acts_with_geo,
        acts_with_precise_geo=row.acts_with_precise_geo,
    )


@router.get("/regions", response_model=list[RegionAnalyticsResponse])
async def region_analytics(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cpv_prefix: str | None = Query(default=None),
    cpv_prefixes: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    nuts_code: str | None = Query(default=None),
    municipality: str | None = Query(default=None),
    conn: AsyncConnection = Depends(get_conn),
) -> list[RegionAnalyticsResponse]:
    """Aggregate market activity by current Greek NUTS-2 region.

    One act may legitimately contribute to more than one region when its
    place of performance spans regions. It is counted once per region.
    """
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH regional_acts AS (
                    SELECT DISTINCT
                        a.id AS act_id,
                        UPPER(LEFT(aloc.nuts_code, 4)) AS nuts_code,
                        a.act_type,
                        COALESCE(a.amount_gross, a.amount_net) AS amount
                    FROM procurement_acts a
                    JOIN act_locations aloc ON aloc.act_id = a.id
                    WHERE a.is_current = TRUE
                      AND procintel_act_is_analytics_eligible(a.id)
                      AND aloc.nuts_code IS NOT NULL
                      AND UPPER(aloc.nuts_code) LIKE 'EL%'
                      AND LENGTH(aloc.nuts_code) >= 4
                      AND (
                          CAST(:nuts_code AS TEXT) IS NULL
                          OR UPPER(aloc.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                      )
                      AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) >= CAST(:date_from AS DATE))
                      AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) <= CAST(:date_to AS DATE))
                      AND procintel_taxonomy_match(
                          a.id,
                          a.title,
                          CAST(:cpv_likes AS TEXT[]),
                          CAST(:keyword_patterns AS TEXT[]),
                          CAST(:taxonomy_match_all AS BOOLEAN)
                      )
                      AND (
                          CAST(:municipality_like AS TEXT) IS NULL
                          OR EXISTS (
                              SELECT 1 FROM act_locations municipality_loc
                              WHERE municipality_loc.act_id = a.id
                                AND (
                                    municipality_loc.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                    OR municipality_loc.place_text ILIKE CAST(:municipality_like AS TEXT)
                                )
                          )
                      )
                )
                SELECT
                    nuts_code,
                    COUNT(*) AS act_count,
                    COUNT(*) FILTER (WHERE act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')) AS opportunity_count,
                    COUNT(*) FILTER (WHERE act_type = 'NOTICE') AS notice_count,
                    COUNT(*) FILTER (WHERE act_type = 'CONTRACT') AS contract_count,
                    SUM(amount) FILTER (WHERE act_type = 'CONTRACT') AS recorded_contract_value
                FROM regional_acts
                WHERE nuts_code = ANY(CAST(:nuts_codes AS TEXT[]))
                GROUP BY nuts_code
                ORDER BY act_count DESC, nuts_code
                """
            ),
            {
                "date_from": date_from,
                "date_to": date_to,
                **_market_filter_params(
                    cpv_prefix=cpv_prefix, cpv_prefixes=cpv_prefixes,
                    keyword=keyword, keywords=keywords, taxonomy_match=taxonomy_match,
                ),
                "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "nuts_codes": list(GREEK_NUTS_2_NAMES),
            },
        )
    ).all()
    return [
        RegionAnalyticsResponse(
            nuts_code=row.nuts_code,
            region_name=GREEK_NUTS_2_NAMES[row.nuts_code],
            act_count=row.act_count,
            opportunity_count=row.opportunity_count,
            notice_count=row.notice_count,
            contract_count=row.contract_count,
            recorded_contract_value=row.recorded_contract_value,
        )
        for row in rows
    ]


@router.get("/opportunities", response_model=list[OpportunityResponse])
async def opportunities(
    keyword: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cpv_prefix: str | None = Query(default=None),
    cpv_prefixes: str | None = Query(default=None),
    nuts_code: str | None = Query(default=None),
    municipality: str | None = Query(default=None),
    amount_min: Decimal | None = Query(default=None, ge=0),
    limit: int = Query(default=20, gt=0, le=100),
    conn: AsyncConnection = Depends(get_conn),
) -> list[OpportunityResponse]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT
                    a.id AS act_id,
                    a.process_id,
                    a.title,
                    a.act_type,
                    COALESCE(process_buyer.canonical_name, act_buyer.canonical_name) AS buyer_name,
                    COALESCE(a.amount_gross, a.amount_net) AS amount_gross,
                    COALESCE(a.publication_date, a.submission_date, a.decision_date) AS event_date,
                    a.submission_date,
                    COALESCE(array_remove(array_agg(DISTINCT acpv.cpv_code), NULL), ARRAY[]::TEXT[]) AS cpv_codes,
                    COALESCE(array_remove(array_agg(DISTINCT aloc.nuts_code), NULL), ARRAY[]::TEXT[]) AS nuts_codes,
                    COALESCE(
                        array_remove(
                            array_agg(DISTINCT COALESCE(
                                aloc.municipality_name,
                                aloc.regional_unit_name,
                                aloc.region_name,
                                aloc.place_text
                            )),
                            NULL
                        ),
                        ARRAY[]::TEXT[]
                    ) AS location_labels
                FROM procurement_acts a
                LEFT JOIN procurement_processes p ON p.id = a.process_id
                LEFT JOIN entities process_buyer ON process_buyer.id = p.buyer_entity_id
                LEFT JOIN LATERAL (
                    SELECT e.canonical_name
                    FROM act_parties ap
                    JOIN entities e ON e.id = ap.entity_id
                    WHERE ap.act_id = a.id
                      AND ap.party_role = 'BUYER'
                    ORDER BY e.canonical_name
                    LIMIT 1
                ) act_buyer ON TRUE
                LEFT JOIN act_cpv_codes acpv ON acpv.act_id = a.id
                LEFT JOIN act_locations aloc ON aloc.act_id = a.id
                WHERE a.is_current = TRUE
                  AND procintel_act_is_analytics_eligible(a.id)
                  AND a.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')
                  AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(a.publication_date, a.submission_date, a.decision_date) >= CAST(:date_from AS DATE))
                  AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(a.publication_date, a.submission_date, a.decision_date) <= CAST(:date_to AS DATE))
                  AND (CAST(:amount_min AS NUMERIC) IS NULL OR COALESCE(a.amount_gross, a.amount_net, 0) >= CAST(:amount_min AS NUMERIC))
                  AND procintel_taxonomy_match(
                      a.id,
                      a.title,
                      CAST(:cpv_likes AS TEXT[]),
                      CAST(:keyword_patterns AS TEXT[]),
                      CAST(:taxonomy_match_all AS BOOLEAN)
                  )
                  AND (
                      CAST(:nuts_code AS TEXT) IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM act_locations aloc_filter
                          WHERE aloc_filter.act_id = a.id
                            AND UPPER(aloc_filter.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                      )
                  )
                  AND (
                      CAST(:municipality_like AS TEXT) IS NULL
                      OR EXISTS (
                          SELECT 1 FROM act_locations municipality_loc
                          WHERE municipality_loc.act_id = a.id
                            AND (
                                municipality_loc.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                OR municipality_loc.place_text ILIKE CAST(:municipality_like AS TEXT)
                            )
                      )
                  )
                GROUP BY
                    a.id,
                    a.process_id,
                    a.title,
                    a.act_type,
                    process_buyer.canonical_name,
                    act_buyer.canonical_name,
                    a.amount_gross,
                    a.amount_net,
                    a.publication_date,
                    a.submission_date,
                    a.decision_date
                ORDER BY
                    COALESCE(a.publication_date, a.submission_date, a.decision_date) DESC NULLS LAST,
                    a.amount_gross DESC NULLS LAST,
                    a.id
                LIMIT :limit
                """
            ),
            {
                "date_from": date_from,
                "date_to": date_to,
                **_market_filter_params(
                    cpv_prefix=cpv_prefix, cpv_prefixes=cpv_prefixes,
                    keyword=keyword, keywords=keywords, taxonomy_match=taxonomy_match,
                ),
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
                "amount_min": amount_min,
                "limit": limit,
            },
        )
    ).all()

    responses: list[OpportunityResponse] = []
    for row in rows:
        cpv_codes = list(row.cpv_codes or [])
        nuts_codes = list(row.nuts_codes or [])
        fit_score, evidence = _score_opportunity(
            act_type=row.act_type,
            amount_gross=row.amount_gross,
            submission_date=row.submission_date,
            cpv_codes=cpv_codes,
            nuts_codes=nuts_codes,
            cpv_prefix=cpv_prefix,
            nuts_code=nuts_code,
            amount_min=amount_min,
        )
        responses.append(
            OpportunityResponse(
                act_id=str(row.act_id),
                process_id=str(row.process_id) if row.process_id else None,
                title=row.title,
                act_type=row.act_type,
                buyer_name=row.buyer_name,
                amount_gross=row.amount_gross,
                event_date=row.event_date,
                submission_date=row.submission_date,
                cpv_codes=cpv_codes,
                nuts_codes=nuts_codes,
                location_labels=list(row.location_labels or []),
                fit_score=fit_score,
                evidence=evidence,
            )
        )
    return responses


@router.get("/region-activity", response_model=list[RegionActivityResponse])
async def region_activity(
    act_types: str | None = Query(default=None, description="comma-separated, e.g. CONTRACT,NOTICE"),
    keyword: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cpv_prefix: str | None = Query(default=None),
    cpv_prefixes: str | None = Query(default=None),
    nuts_code: str | None = Query(default=None),
    municipality: str | None = Query(default=None),
    amount_min: Decimal | None = Query(default=None, ge=0),
    limit: int = Query(default=30, gt=0, le=100),
    conn: AsyncConnection = Depends(get_conn),
) -> list[RegionActivityResponse]:
    """The map's "what exists here" drill-down: every act in the clicked NUTS
    region, filtered by the same taxonomy/date/amount scope the rest of the
    Analytics tab uses — deliberately not restricted to opportunity act
    types like `/opportunities`, since a region click is about browsing
    everything recorded there (contracts included), not just open bids."""
    act_type_list = [value.strip().upper() for value in (act_types or "").split(",") if value.strip()]
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT
                    a.id AS act_id,
                    a.process_id,
                    a.title,
                    a.act_type,
                    a.status,
                    COALESCE(process_buyer.canonical_name, act_buyer.canonical_name) AS buyer_name,
                    COALESCE(a.amount_gross, a.amount_net) AS amount_gross,
                    COALESCE(a.decision_date, a.publication_date, a.submission_date) AS event_date,
                    COALESCE(array_remove(array_agg(DISTINCT acpv.cpv_code), NULL), ARRAY[]::TEXT[]) AS cpv_codes,
                    COALESCE(array_remove(array_agg(DISTINCT aloc.nuts_code), NULL), ARRAY[]::TEXT[]) AS nuts_codes,
                    COALESCE(
                        array_remove(
                            array_agg(DISTINCT COALESCE(
                                aloc.municipality_name,
                                aloc.regional_unit_name,
                                aloc.region_name,
                                aloc.place_text
                            )),
                            NULL
                        ),
                        ARRAY[]::TEXT[]
                    ) AS location_labels
                FROM procurement_acts a
                LEFT JOIN procurement_processes p ON p.id = a.process_id
                LEFT JOIN entities process_buyer ON process_buyer.id = p.buyer_entity_id
                LEFT JOIN LATERAL (
                    SELECT e.canonical_name
                    FROM act_parties ap
                    JOIN entities e ON e.id = ap.entity_id
                    WHERE ap.act_id = a.id
                      AND ap.party_role = 'BUYER'
                    ORDER BY e.canonical_name
                    LIMIT 1
                ) act_buyer ON TRUE
                LEFT JOIN act_cpv_codes acpv ON acpv.act_id = a.id
                LEFT JOIN act_locations aloc ON aloc.act_id = a.id
                WHERE a.is_current = TRUE
                  AND procintel_act_is_analytics_eligible(a.id)
                  AND (CARDINALITY(CAST(:act_types AS TEXT[])) = 0 OR a.act_type = ANY(CAST(:act_types AS TEXT[])))
                  AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) >= CAST(:date_from AS DATE))
                  AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(a.decision_date, a.publication_date, a.submission_date) <= CAST(:date_to AS DATE))
                  AND (CAST(:amount_min AS NUMERIC) IS NULL OR COALESCE(a.amount_gross, a.amount_net, 0) >= CAST(:amount_min AS NUMERIC))
                  AND procintel_taxonomy_match(
                      a.id,
                      a.title,
                      CAST(:cpv_likes AS TEXT[]),
                      CAST(:keyword_patterns AS TEXT[]),
                      CAST(:taxonomy_match_all AS BOOLEAN)
                  )
                  AND (
                      CAST(:nuts_code AS TEXT) IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM act_locations aloc_filter
                          WHERE aloc_filter.act_id = a.id
                            AND UPPER(aloc_filter.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                      )
                  )
                  AND (
                      CAST(:municipality_like AS TEXT) IS NULL
                      OR EXISTS (
                          SELECT 1 FROM act_locations municipality_loc
                          WHERE municipality_loc.act_id = a.id
                            AND (
                                municipality_loc.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                                OR municipality_loc.place_text ILIKE CAST(:municipality_like AS TEXT)
                            )
                      )
                  )
                GROUP BY
                    a.id,
                    a.process_id,
                    a.title,
                    a.act_type,
                    a.status,
                    process_buyer.canonical_name,
                    act_buyer.canonical_name,
                    a.amount_gross,
                    a.amount_net,
                    a.decision_date,
                    a.publication_date,
                    a.submission_date
                ORDER BY
                    COALESCE(a.decision_date, a.publication_date, a.submission_date) DESC NULLS LAST,
                    a.amount_gross DESC NULLS LAST,
                    a.id
                LIMIT :limit
                """
            ),
            {
                "act_types": act_type_list,
                "date_from": date_from,
                "date_to": date_to,
                **_market_filter_params(
                    cpv_prefix=cpv_prefix, cpv_prefixes=cpv_prefixes,
                    keyword=keyword, keywords=keywords, taxonomy_match=taxonomy_match,
                ),
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "municipality_like": f"%{municipality.strip()}%" if municipality and municipality.strip() else None,
                "amount_min": amount_min,
                "limit": limit,
            },
        )
    ).all()
    return [
        RegionActivityResponse(
            act_id=str(row.act_id),
            process_id=str(row.process_id) if row.process_id else None,
            title=row.title,
            act_type=row.act_type,
            status=row.status,
            buyer_name=row.buyer_name,
            amount_gross=row.amount_gross,
            event_date=row.event_date,
            cpv_codes=list(row.cpv_codes or []),
            nuts_codes=list(row.nuts_codes or []),
            location_labels=list(row.location_labels or []),
        )
        for row in rows
    ]


@router.get("/locations", response_model=list[GeocodedLocationAnalyticsResponse])
async def geocoded_locations(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cpv_prefix: str | None = Query(default=None),
    cpv_prefixes: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    keywords: str | None = Query(default=None),
    taxonomy_match: str | None = Query(default=None, max_length=32),
    nuts_code: str | None = Query(default=None),
    municipality: str | None = Query(default=None),
    limit: int = Query(default=500, gt=0, le=1000),
    conn: AsyncConnection = Depends(get_conn),
) -> list[GeocodedLocationAnalyticsResponse]:
    """Aggregate precise place-of-performance points for the interactive map."""
    municipality_like = f"%{municipality.strip()}%" if municipality and municipality.strip() else None
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH point_acts AS (
                    SELECT DISTINCT
                        a.id AS act_id,
                        a.act_type,
                        COALESCE(a.amount_gross, a.amount_net) AS amount,
                        COALESCE(
                            aloc.municipality_name,
                            aloc.place_text,
                            aloc.regional_unit_name,
                            aloc.region_name,
                            'Άγνωστη τοποθεσία'
                        ) AS label,
                        aloc.municipality_name,
                        aloc.regional_unit_name,
                        aloc.region_name,
                        aloc.nuts_code,
                        ROUND(ST_Y(ST_PointOnSurface(aloc.geom))::numeric, 5) AS latitude,
                        ROUND(ST_X(ST_PointOnSurface(aloc.geom))::numeric, 5) AS longitude,
                        aloc.confidence
                    FROM procurement_acts a
                    JOIN act_locations aloc ON aloc.act_id = a.id
                    WHERE a.is_current = TRUE
                      AND procintel_act_is_analytics_eligible(a.id)
                      AND aloc.geom IS NOT NULL
                      AND (CAST(:date_from AS DATE) IS NULL OR COALESCE(a.publication_date, a.submission_date, a.decision_date) >= CAST(:date_from AS DATE))
                      AND (CAST(:date_to AS DATE) IS NULL OR COALESCE(a.publication_date, a.submission_date, a.decision_date) <= CAST(:date_to AS DATE))
                      AND procintel_taxonomy_match(
                          a.id,
                          a.title,
                          CAST(:cpv_likes AS TEXT[]),
                          CAST(:keyword_patterns AS TEXT[]),
                          CAST(:taxonomy_match_all AS BOOLEAN)
                      )
                      AND (
                          CAST(:nuts_code AS TEXT) IS NULL
                          OR UPPER(aloc.nuts_code) LIKE CAST(:nuts_like AS TEXT)
                      )
                      AND (
                          CAST(:municipality_like AS TEXT) IS NULL
                          OR aloc.municipality_name ILIKE CAST(:municipality_like AS TEXT)
                          OR aloc.place_text ILIKE CAST(:municipality_like AS TEXT)
                      )
                )
                SELECT
                    label,
                    municipality_name,
                    regional_unit_name,
                    region_name,
                    nuts_code,
                    latitude,
                    longitude,
                    COUNT(DISTINCT act_id) AS act_count,
                    COUNT(DISTINCT act_id) FILTER (
                        WHERE act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')
                    ) AS opportunity_count,
                    COUNT(DISTINCT act_id) FILTER (WHERE act_type = 'CONTRACT') AS contract_count,
                    SUM(amount) FILTER (WHERE act_type = 'CONTRACT') AS recorded_contract_value,
                    MIN(confidence) AS minimum_confidence
                FROM point_acts
                GROUP BY label, municipality_name, regional_unit_name, region_name, nuts_code, latitude, longitude
                ORDER BY act_count DESC, label
                LIMIT :limit
                """
            ),
            {
                "date_from": date_from,
                "date_to": date_to,
                **_market_filter_params(
                    cpv_prefix=cpv_prefix, cpv_prefixes=cpv_prefixes,
                    keyword=keyword, keywords=keywords, taxonomy_match=taxonomy_match,
                ),
                "nuts_code": nuts_code,
                "nuts_like": f"{nuts_code.upper()}%" if nuts_code else None,
                "municipality_like": municipality_like,
                "limit": limit,
            },
        )
    ).all()
    return [
        GeocodedLocationAnalyticsResponse(
            label=row.label,
            nuts_code=row.nuts_code,
            municipality_name=row.municipality_name,
            regional_unit_name=row.regional_unit_name,
            region_name=row.region_name,
            latitude=float(row.latitude),
            longitude=float(row.longitude),
            act_count=row.act_count,
            opportunity_count=row.opportunity_count,
            contract_count=row.contract_count,
            recorded_contract_value=row.recorded_contract_value,
            minimum_confidence=float(row.minimum_confidence) if row.minimum_confidence is not None else None,
        )
        for row in rows
    ]
