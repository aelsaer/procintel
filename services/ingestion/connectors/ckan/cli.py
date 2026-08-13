"""Manual CLI entrypoint.

    python -m services.ingestion.connectors.ckan.cli sync-population \\
        --dataset-id <ckan-dataset-slug> --reference-year 2024

    python -m services.ingestion.connectors.ckan.cli sync-boundaries \\
        --dataset-id <ckan-dataset-slug> --boundary-type MUNICIPALITY

    python -m services.ingestion.connectors.ckan.cli sync-metric \\
        --dataset-id <ckan-dataset-slug> --metric-name GDP_PER_CAPITA \\
        --reference-year 2024 [--value-field gdpPerCapita]

    python -m services.ingestion.connectors.ckan.cli sync-facilities \\
        --dataset-id <ckan-dataset-slug> --facility-type SCHOOL \\
        --capacity-metric STUDENTS [--capacity-field students]

Onboards (or refreshes) one CKAN dataset into `external_datasets`, downloads
its first CSV/GeoJSON resource, and writes population/regional-indicator
`geo_denominators` rows, administrative-boundary rows, or school/hospital
`facilities` rows. Standalone like TED's CLI — nothing on the ΚΗΜΔΗΣ side
triggers a catalog refresh; onboarding new datasets/adapters is an operator
action, not a pipeline trigger. `sync-population` is a documented shortcut
for `sync-metric --metric-name POPULATION` — the common case, kept as its
own subcommand for readability; `sync-metric` is the generic form for any
other regional economic indicator (§22.2) sharing the exact same file
shape.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from packages.source_clients.raw_store import configured_raw_store

from .client import CkanClient
from .config import CkanConnectorConfig
from .db_writer import (
    ingest_boundaries_dataset,
    ingest_facilities_dataset,
    ingest_metric_dataset,
    ingest_population_dataset,
)
from .facilities import DEFAULT_CAPACITY_FIELD_CANDIDATES, STUDENT_COMPONENT_FIELDS
from .normalize import DEFAULT_VALUE_FIELD_CANDIDATES
from .registry import upsert_external_dataset
from .validation import record_validation, validate_resource

CATALOG_SOURCE = "DATA_GOV_GR"
BOUNDARY_TYPES = ("MUNICIPALITY", "REGION", "REGIONAL_UNIT", "POSTAL_CODE", "ENVIRONMENTAL_ZONE")
FACILITY_TYPES = ("SCHOOL", "HOSPITAL")
DEFAULT_DATASETS = (
    {
        "dataset_id": "gis-ypen-floods-wms-adm_pol_dimotikes_enotites",
        "adapter": "boundaries",
        "boundary_type": "MUNICIPALITY",
        "coverage": "GREECE",
    },
    {
        "dataset_id": "minedu_students_school",
        "adapter": "facilities",
        "facility_type": "SCHOOL",
        "capacity_metric": "STUDENTS",
        "capacity_field": None,
        "coverage": "GREECE",
    },
)


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _validate_download(
    conn: AsyncConnection,
    *,
    dataset_id: str,
    external_dataset_id,
    adapter_name: str,
    resource_url: str,
    content: bytes,
    required_column_groups: tuple[tuple[str, ...], ...] = (),
) -> None:
    validation = validate_resource(
        content,
        adapter_name=adapter_name,
        required_column_groups=required_column_groups,
    )
    await record_validation(
        conn,
        external_dataset_id=external_dataset_id,
        adapter_name=adapter_name,
        resource_url=resource_url,
        validation=validation,
    )
    if validation.status != "VALID":
        await conn.commit()
        raise RuntimeError(
            f"dataset {dataset_id!r} failed live schema validation: "
            + "; ".join(validation.errors)
        )


async def _sync_population(dataset_id: str, reference_year: int, database_url: str, raw_root: str) -> None:
    config = CkanConnectorConfig.from_env()
    client = CkanClient(config)
    raw_store = configured_raw_store(raw_root)
    engine = create_async_engine(_to_asyncpg_url(database_url))

    try:
        package = await client.package_show(dataset_id)
        csv_resources = [r for r in package.resources if (r.get("format") or "").upper() == "CSV"]
        if not csv_resources:
            raise RuntimeError(f"dataset {dataset_id!r} has no CSV resource to ingest")
        resource_url = csv_resources[0]["url"]

        async with engine.connect() as conn:
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source=CATALOG_SOURCE,
                package=package,
                resource_type="CSV",
                resource_url=resource_url,
                adapter_name="population",
                config={"reference_year": reference_year},
            )
            await conn.commit()

            resource_response = await client.fetch_resource_bytes(resource_url)
            await _validate_download(
                conn,
                dataset_id=dataset_id,
                external_dataset_id=registry_result.external_dataset_id,
                adapter_name="population",
                resource_url=resource_url,
                content=resource_response.content,
                required_column_groups=(
                    ("kallikratis_code", "municipality_code", "dimos_code", "nuts_code", "nuts3", "nuts"),
                    DEFAULT_VALUE_FIELD_CANDIDATES,
                ),
            )
            raw_ref = await raw_store.put(
                source="ckan",
                resource="population",
                partition_key=f"dataset={dataset_id}",
                payload=resource_response.content,
            )
            ingest_result = await ingest_population_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                reference_year=reference_year,
                csv_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=resource_response.http_status,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            print(
                f"dataset={dataset_id} onboarded={registry_result.is_new} "
                f"rows_written={ingest_result.rows_written}"
            )
    finally:
        await client.aclose()
        await engine.dispose()


async def _sync_boundaries(dataset_id: str, boundary_type: str, database_url: str, raw_root: str) -> None:
    config = CkanConnectorConfig.from_env()
    client = CkanClient(config)
    raw_store = configured_raw_store(raw_root)
    engine = create_async_engine(_to_asyncpg_url(database_url))

    try:
        package = await client.package_show(dataset_id)
        geojson_resources = [
            r for r in package.resources if (r.get("format") or "").upper() in ("GEOJSON", "JSON")
        ]
        if not geojson_resources:
            raise RuntimeError(f"dataset {dataset_id!r} has no GeoJSON resource to ingest")
        resource_url = geojson_resources[0]["url"]

        async with engine.connect() as conn:
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source=CATALOG_SOURCE,
                package=package,
                resource_type="GEOJSON",
                resource_url=resource_url,
                adapter_name="boundaries",
                config={"boundary_type": boundary_type},
            )
            await conn.commit()

            resource_response = await client.fetch_resource_bytes(resource_url)
            await _validate_download(
                conn,
                dataset_id=dataset_id,
                external_dataset_id=registry_result.external_dataset_id,
                adapter_name="boundaries",
                resource_url=resource_url,
                content=resource_response.content,
            )
            raw_ref = await raw_store.put(
                source="ckan",
                resource="administrative_boundary",
                partition_key=f"dataset={dataset_id}",
                payload=resource_response.content,
            )
            ingest_result = await ingest_boundaries_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                boundary_type=boundary_type,
                geojson_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=resource_response.http_status,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            print(
                f"dataset={dataset_id} onboarded={registry_result.is_new} "
                f"rows_written={ingest_result.rows_written}"
            )
    finally:
        await client.aclose()
        await engine.dispose()


async def _sync_metric(
    dataset_id: str, metric_name: str, reference_year: int, value_field: str | None, database_url: str, raw_root: str
) -> None:
    config = CkanConnectorConfig.from_env()
    client = CkanClient(config)
    raw_store = configured_raw_store(raw_root)
    engine = create_async_engine(_to_asyncpg_url(database_url))
    value_field_candidates = (value_field, *DEFAULT_VALUE_FIELD_CANDIDATES) if value_field else DEFAULT_VALUE_FIELD_CANDIDATES

    try:
        package = await client.package_show(dataset_id)
        csv_resources = [r for r in package.resources if (r.get("format") or "").upper() == "CSV"]
        if not csv_resources:
            raise RuntimeError(f"dataset {dataset_id!r} has no CSV resource to ingest")
        resource_url = csv_resources[0]["url"]

        async with engine.connect() as conn:
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source=CATALOG_SOURCE,
                package=package,
                resource_type="CSV",
                resource_url=resource_url,
                adapter_name="metric",
                config={
                    "metric_name": metric_name,
                    "reference_year": reference_year,
                    "value_field": value_field,
                },
            )
            await conn.commit()

            resource_response = await client.fetch_resource_bytes(resource_url)
            await _validate_download(
                conn,
                dataset_id=dataset_id,
                external_dataset_id=registry_result.external_dataset_id,
                adapter_name="metric",
                resource_url=resource_url,
                content=resource_response.content,
                required_column_groups=(
                    ("kallikratis_code", "municipality_code", "dimos_code", "nuts_code", "nuts3", "nuts"),
                    value_field_candidates,
                ),
            )
            raw_ref = await raw_store.put(
                source="ckan",
                resource=f"metric_{metric_name.lower()}",
                partition_key=f"dataset={dataset_id}",
                payload=resource_response.content,
            )
            ingest_result = await ingest_metric_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                metric_name=metric_name,
                reference_year=reference_year,
                csv_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=resource_response.http_status,
                fetched_at=datetime.now(timezone.utc),
                value_field_candidates=value_field_candidates,
                resource_type=f"geo_metric_{metric_name.lower()}",
            )
            await conn.commit()
            print(
                f"dataset={dataset_id} metric={metric_name} onboarded={registry_result.is_new} "
                f"rows_written={ingest_result.rows_written}"
            )
    finally:
        await client.aclose()
        await engine.dispose()


async def _sync_facilities(
    dataset_id: str,
    facility_type: str,
    capacity_metric: str | None,
    capacity_field: str | None,
    database_url: str,
    raw_root: str,
) -> None:
    config = CkanConnectorConfig.from_env()
    client = CkanClient(config)
    raw_store = configured_raw_store(raw_root)
    engine = create_async_engine(_to_asyncpg_url(database_url))
    capacity_field_candidates = (
        (capacity_field, *DEFAULT_CAPACITY_FIELD_CANDIDATES) if capacity_field else DEFAULT_CAPACITY_FIELD_CANDIDATES
    )

    try:
        package = await client.package_show(dataset_id)
        csv_resources = [r for r in package.resources if (r.get("format") or "").upper() == "CSV"]
        if not csv_resources:
            raise RuntimeError(f"dataset {dataset_id!r} has no CSV resource to ingest")
        resource_url = csv_resources[0]["url"]

        async with engine.connect() as conn:
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source=CATALOG_SOURCE,
                package=package,
                resource_type="CSV",
                resource_url=resource_url,
                adapter_name="facilities",
                config={
                    "facility_type": facility_type,
                    "capacity_metric": capacity_metric,
                    "capacity_field": capacity_field,
                },
            )
            await conn.commit()

            resource_response = await client.fetch_resource_bytes(resource_url)
            capacity_columns = (
                (*capacity_field_candidates, *STUDENT_COMPONENT_FIELDS)
                if capacity_metric
                else ()
            )
            required_groups = [
                (
                    "code",
                    "facility_code",
                    "school_code",
                    "hospital_code",
                    "name",
                    "facility_name",
                    "school_name",
                    "hospital_name",
                )
            ]
            if capacity_columns:
                required_groups.append(capacity_columns)
            await _validate_download(
                conn,
                dataset_id=dataset_id,
                external_dataset_id=registry_result.external_dataset_id,
                adapter_name="facilities",
                resource_url=resource_url,
                content=resource_response.content,
                required_column_groups=tuple(required_groups),
            )
            raw_ref = await raw_store.put(
                source="ckan",
                resource=f"facility_{facility_type.lower()}",
                partition_key=f"dataset={dataset_id}",
                payload=resource_response.content,
            )
            ingest_result = await ingest_facilities_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                facility_type=facility_type,
                capacity_metric=capacity_metric,
                csv_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=resource_response.http_status,
                fetched_at=datetime.now(timezone.utc),
                capacity_field_candidates=capacity_field_candidates,
            )
            await conn.commit()
            print(
                f"dataset={dataset_id} facility_type={facility_type} onboarded={registry_result.is_new} "
                f"rows_written={ingest_result.rows_written}"
            )
    finally:
        await client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="CKAN / data.gov.gr connector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_population = subparsers.add_parser("sync-population")
    sync_population.add_argument("--dataset-id", required=True, help="CKAN catalog dataset id/slug")
    sync_population.add_argument("--reference-year", required=True, type=int)
    sync_population.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    sync_population.add_argument("--raw-root", default="./raw", help="local raw-storage root")

    sync_boundaries = subparsers.add_parser("sync-boundaries")
    sync_boundaries.add_argument("--dataset-id", required=True, help="CKAN catalog dataset id/slug")
    sync_boundaries.add_argument("--boundary-type", required=True, choices=BOUNDARY_TYPES)
    sync_boundaries.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    sync_boundaries.add_argument("--raw-root", default="./raw", help="local raw-storage root")

    sync_metric = subparsers.add_parser("sync-metric")
    sync_metric.add_argument("--dataset-id", required=True, help="CKAN catalog dataset id/slug")
    sync_metric.add_argument("--metric-name", required=True, help="e.g. GDP_PER_CAPITA, UNEMPLOYMENT_RATE")
    sync_metric.add_argument("--reference-year", required=True, type=int)
    sync_metric.add_argument(
        "--value-field", default=None, help="CSV column holding the value, if not population/plithysmos/value"
    )
    sync_metric.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    sync_metric.add_argument("--raw-root", default="./raw", help="local raw-storage root")

    sync_facilities = subparsers.add_parser("sync-facilities")
    sync_facilities.add_argument("--dataset-id", required=True, help="CKAN catalog dataset id/slug")
    sync_facilities.add_argument("--facility-type", required=True, choices=FACILITY_TYPES)
    sync_facilities.add_argument(
        "--capacity-metric", default=None, help="e.g. STUDENTS for schools, BEDS for hospitals"
    )
    sync_facilities.add_argument(
        "--capacity-field", default=None, help="CSV column holding the capacity value, if not capacity/students/beds"
    )
    sync_facilities.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    sync_facilities.add_argument("--raw-root", default="./raw", help="local raw-storage root")

    onboard_defaults = subparsers.add_parser(
        "onboard-defaults",
        help="validate and ingest the maintained data.gov.gr dataset manifest",
    )
    onboard_defaults.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    onboard_defaults.add_argument("--raw-root", default="./raw", help="local raw-storage root")

    args = parser.parse_args()
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("--database-url or $DATABASE_URL is required")

    if args.command == "onboard-defaults":
        for dataset in DEFAULT_DATASETS:
            if dataset["adapter"] == "boundaries":
                asyncio.run(
                    _sync_boundaries(
                        dataset["dataset_id"],
                        dataset["boundary_type"],
                        database_url,
                        args.raw_root,
                    )
                )
            elif dataset["adapter"] == "facilities":
                asyncio.run(
                    _sync_facilities(
                        dataset["dataset_id"],
                        dataset["facility_type"],
                        dataset.get("capacity_metric"),
                        dataset.get("capacity_field"),
                        database_url,
                        args.raw_root,
                    )
                )
    elif args.command == "sync-boundaries":
        asyncio.run(_sync_boundaries(args.dataset_id, args.boundary_type, database_url, args.raw_root))
    elif args.command == "sync-metric":
        asyncio.run(
            _sync_metric(
                args.dataset_id, args.metric_name, args.reference_year, args.value_field, database_url, args.raw_root
            )
        )
    elif args.command == "sync-facilities":
        asyncio.run(
            _sync_facilities(
                args.dataset_id,
                args.facility_type,
                args.capacity_metric,
                args.capacity_field,
                database_url,
                args.raw_root,
            )
        )
    else:
        asyncio.run(_sync_population(args.dataset_id, args.reference_year, database_url, args.raw_root))


if __name__ == "__main__":
    main()
