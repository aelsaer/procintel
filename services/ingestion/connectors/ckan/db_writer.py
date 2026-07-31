"""Idempotent writes for the CKAN adapters built so far: population/
regional-indicator `geo_denominators` rows, administrative-boundary `geom`
rows, and school/hospital `facilities` rows.

One CKAN resource download = one `source_records` row (dedup on
external dataset + resource type + content hash). Unlike the
per-record upsert pattern elsewhere, every one of these is a whole-dataset
snapshot with no natural per-row identifier to upsert on — a *changed*
file (different content hash) replaces every row for that dataset (scoped
by `external_dataset_id` + `reference_year`/`boundary_type`/
`facility_type`) wholesale rather than merging row by row. An unchanged
file is a pure no-op, same dedup guarantee as every other connector.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from geoalchemy2 import WKTElement
import sqlalchemy as sa
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    administrative_boundaries,
    external_datasets,
    facilities,
    geo_denominators,
    source_records,
)

from .boundaries import normalize_boundaries_geojson
from .facilities import DEFAULT_CAPACITY_FIELD_CANDIDATES, normalize_facilities_csv
from .normalize import DEFAULT_VALUE_FIELD_CANDIDATES, normalize_metric_csv

METRIC_NAME = "POPULATION"
BOUNDARIES_SRID = 4326


@dataclass(frozen=True)
class PopulationIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    rows_written: int


async def _dataset_license(conn: AsyncConnection, external_dataset_id: uuid.UUID) -> str | None:
    return (
        await conn.execute(
            select(external_datasets.c.license_code).where(
                external_datasets.c.id == external_dataset_id
            )
        )
    ).scalar() or "UNCONFIRMED"


async def ingest_metric_dataset(
    conn: AsyncConnection,
    *,
    external_dataset_id: uuid.UUID,
    metric_name: str,
    reference_year: int,
    csv_bytes: bytes,
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
    value_field_candidates: tuple[str, ...] = DEFAULT_VALUE_FIELD_CANDIDATES,
    resource_type: str = "geo_metric",
) -> PopulationIngestResult:
    """Generic entry point behind `ingest_population_dataset()` — population
    and regional-economic-indicator files (§22.2) share the exact same
    whole-dataset-snapshot shape and idempotency rule, differing only in
    which `geo_denominators.metric_name` they write and which CSV column
    holds the value. `resource_type` scopes the `source_records` dedup
    namespace per metric family so two different metrics' files never
    collide on content hash lookup."""
    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "CKAN",
                source_records.c.resource_type == resource_type,
                source_records.c.source_native_id
                == str(external_dataset_id),
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_seen is not None:
        return PopulationIngestResult(source_record_id=None, rows_written=0)

    normalized_rows = normalize_metric_csv(csv_bytes, value_field_candidates=value_field_candidates)

    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="CKAN",
            resource_type=resource_type,
            source_native_id=str(external_dataset_id),
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code=await _dataset_license(conn, external_dataset_id),
            parse_status="PARSED",
        )
    )

    # whole-file replace: this exact reference_year's rows for this dataset
    # are stale the moment a differently-hashed file has been ingested
    await conn.execute(
        delete(geo_denominators).where(
            geo_denominators.c.external_dataset_id == external_dataset_id,
            geo_denominators.c.metric_name == metric_name,
            geo_denominators.c.reference_year == reference_year,
        )
    )

    if normalized_rows:
        await conn.execute(
            geo_denominators.insert(),
            [
                dict(
                    id=uuid.uuid4(),
                    metric_name=metric_name,
                    nuts_code=row.nuts_code,
                    municipality_code=row.municipality_code,
                    reference_year=reference_year,
                    value=row.value,
                    external_dataset_id=external_dataset_id,
                    source_record_id=source_record_id,
                )
                for row in normalized_rows
            ],
        )

    return PopulationIngestResult(source_record_id=source_record_id, rows_written=len(normalized_rows))


async def ingest_population_dataset(
    conn: AsyncConnection,
    *,
    external_dataset_id: uuid.UUID,
    reference_year: int,
    csv_bytes: bytes,
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> PopulationIngestResult:
    return await ingest_metric_dataset(
        conn,
        external_dataset_id=external_dataset_id,
        metric_name=METRIC_NAME,
        reference_year=reference_year,
        csv_bytes=csv_bytes,
        payload_uri=payload_uri,
        content_sha256=content_sha256,
        http_status=http_status,
        fetched_at=fetched_at,
        value_field_candidates=DEFAULT_VALUE_FIELD_CANDIDATES,
        resource_type="population",
    )


@dataclass(frozen=True)
class BoundaryIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    rows_written: int


async def ingest_boundaries_dataset(
    conn: AsyncConnection,
    *,
    external_dataset_id: uuid.UUID,
    boundary_type: str,
    geojson_bytes: bytes,
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
) -> BoundaryIngestResult:
    """Same whole-dataset-snapshot idempotency as `ingest_population_dataset`
    — a boundaries file has no natural per-row identifier to upsert on
    either, so a changed file (different content hash) replaces every
    `administrative_boundaries` row for this `(external_dataset_id,
    boundary_type)` combination wholesale."""
    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "CKAN",
                source_records.c.resource_type == "administrative_boundary",
                source_records.c.source_native_id
                == str(external_dataset_id),
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_seen is not None:
        return BoundaryIngestResult(source_record_id=None, rows_written=0)

    normalized_boundaries = normalize_boundaries_geojson(geojson_bytes)

    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="CKAN",
            resource_type="administrative_boundary",
            source_native_id=str(external_dataset_id),
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code=await _dataset_license(conn, external_dataset_id),
            parse_status="PARSED",
        )
    )

    await conn.execute(
        delete(administrative_boundaries).where(
            administrative_boundaries.c.external_dataset_id == external_dataset_id,
            administrative_boundaries.c.boundary_type == boundary_type,
        )
    )

    for boundary in normalized_boundaries:
        source_geometry = WKTElement(boundary.wkt, srid=boundary.source_srid)
        geometry = (
            source_geometry
            if boundary.source_srid == BOUNDARIES_SRID
            else sa.func.ST_Transform(source_geometry, BOUNDARIES_SRID)
        )
        await conn.execute(
            administrative_boundaries.insert().values(
                id=uuid.uuid4(),
                boundary_type=boundary_type,
                external_dataset_id=external_dataset_id,
                code=boundary.code,
                name=boundary.name,
                nuts_code=boundary.nuts_code,
                geom=geometry,
                source_record_id=source_record_id,
            )
        )

    return BoundaryIngestResult(source_record_id=source_record_id, rows_written=len(normalized_boundaries))


@dataclass(frozen=True)
class FacilityIngestResult:
    source_record_id: uuid.UUID | None  # None if deduped (no-op)
    rows_written: int


async def ingest_facilities_dataset(
    conn: AsyncConnection,
    *,
    external_dataset_id: uuid.UUID,
    facility_type: str,
    capacity_metric: str | None,
    csv_bytes: bytes,
    payload_uri: str,
    content_sha256: str,
    http_status: int,
    fetched_at: datetime,
    capacity_field_candidates: tuple[str, ...] = DEFAULT_CAPACITY_FIELD_CANDIDATES,
) -> FacilityIngestResult:
    """Same whole-dataset-snapshot idempotency as the other CKAN adapters —
    a changed file (different content hash) replaces every `facilities` row
    for this `(external_dataset_id, facility_type)` combination wholesale.
    `resource_type` is scoped per facility type so schools and hospitals for
    the same dataset row don't share a dedup namespace."""
    resource_type = f"facility_{facility_type.lower()}"
    already_seen = (
        await conn.execute(
            select(source_records.c.id).where(
                source_records.c.source_system == "CKAN",
                source_records.c.resource_type == resource_type,
                source_records.c.source_native_id
                == str(external_dataset_id),
                source_records.c.content_sha256 == content_sha256,
            )
        )
    ).first()
    if already_seen is not None:
        return FacilityIngestResult(source_record_id=None, rows_written=0)

    normalized_facilities = normalize_facilities_csv(csv_bytes, capacity_field_candidates=capacity_field_candidates)

    source_record_id = uuid.uuid4()
    await conn.execute(
        source_records.insert().values(
            id=source_record_id,
            source_system="CKAN",
            resource_type=resource_type,
            source_native_id=str(external_dataset_id),
            content_sha256=content_sha256,
            payload_uri=payload_uri,
            fetched_at=fetched_at,
            http_status=http_status,
            license_code=await _dataset_license(conn, external_dataset_id),
            parse_status="PARSED",
        )
    )

    await conn.execute(
        delete(facilities).where(
            facilities.c.external_dataset_id == external_dataset_id,
            facilities.c.facility_type == facility_type,
        )
    )

    if normalized_facilities:
        await conn.execute(
            facilities.insert(),
            [
                dict(
                    id=uuid.uuid4(),
                    facility_type=facility_type,
                    external_dataset_id=external_dataset_id,
                    code=facility.code,
                    name=facility.name,
                    nuts_code=facility.nuts_code,
                    municipality_code=facility.municipality_code,
                    capacity_metric=capacity_metric,
                    capacity_value=facility.capacity_value,
                    geom=(
                        WKTElement(f"POINT({facility.longitude} {facility.latitude})", srid=BOUNDARIES_SRID)
                        if facility.latitude is not None and facility.longitude is not None
                        else None
                    ),
                    source_record_id=source_record_id,
                )
                for facility in normalized_facilities
            ],
        )

    return FacilityIngestResult(source_record_id=source_record_id, rows_written=len(normalized_facilities))
