"""Curated secondary data.gov.gr sources with explicit coverage claims."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import data_quality_issues, external_datasets, source_records
from packages.source_clients.raw_store import RawStore, configured_raw_store

from .client import CkanClient, PackageShowResponse
from .config import CkanConnectorConfig
from .registry import upsert_external_dataset

CATALOG_REFRESH_INTERVAL = timedelta(days=7)


@dataclass(frozen=True)
class CuratedCatalogDataset:
    dataset_id: str
    domain: str
    provenance_role: str
    geographic_scope: str
    temporal_scope: str
    completeness_claim: str
    primary_source: bool
    notes: str


CURATED_SUPPLEMENTARY_DATASETS = (
    CuratedCatalogDataset(
        dataset_id="companies-prefecture-type",
        domain="GEMI",
        provenance_role="COMPANY_MARKET_DENOMINATOR",
        geographic_scope="GREECE_BY_PREFECTURE",
        temporal_scope="CATALOG_DECLARED",
        completeness_claim="AGGREGATE_ONLY",
        primary_source=False,
        notes="Company counts only; never used as company-level GEMI enrichment.",
    ),
    CuratedCatalogDataset(
        dataset_id="erga-espa-anaptyxi",
        domain="ANAPTYXI",
        provenance_role="FUNDING_CATALOG_POINTER",
        geographic_scope="GREECE",
        temporal_scope="CATALOG_DECLARED",
        completeness_claim="METADATA_ONLY",
        primary_source=False,
        notes="Catalog package is not a bulk project export; direct ANAPTYXI APIs remain primary.",
    ),
    CuratedCatalogDataset(
        dataset_id="api-ergwn-espa",
        domain="ANAPTYXI",
        provenance_role="LEGACY_API_DOCUMENTATION",
        geographic_scope="GREECE",
        temporal_scope="2007-2013",
        completeness_claim="DOCUMENTATION_ONLY",
        primary_source=False,
        notes="Documents the legacy projects API and provides no current project mirror.",
    ),
    CuratedCatalogDataset(
        dataset_id="diey8ynsh-anapty3hs-monadwn-ygeias-tmhma-a-loipa-stoixeia",
        domain="HEALTH_REFERENCE",
        provenance_role="HISTORICAL_FACILITY_DENOMINATOR",
        geographic_scope="GREECE",
        temporal_scope="2017",
        completeness_claim="HISTORICAL_SNAPSHOT",
        primary_source=False,
        notes="Hospital directory and beds are historical and must always be labelled 2017.",
    ),
)


@dataclass(frozen=True)
class CatalogRefreshOutcome:
    dataset_id: str
    status: str
    resource_url: str | None = None
    error: str | None = None


def choose_catalog_resource(package: PackageShowResponse) -> tuple[str | None, str | None]:
    resources = [resource for resource in package.resources if resource.get("url")]
    if not resources:
        return None, None
    preferred_formats = ("CSV", "GEOJSON", "JSON", "XLSX", "XLS", "XML", "ZIP", "HTML")
    for preferred_format in preferred_formats:
        for resource in resources:
            resource_format = str(resource.get("format") or "").upper()
            if resource_format == preferred_format:
                return str(resource["url"]), resource_format
    resource = resources[0]
    return str(resource["url"]), str(resource.get("format") or "UNKNOWN").upper()


def catalog_resource_provenance(
    package: PackageShowResponse,
    resource_url: str | None,
) -> dict[str, Any]:
    if resource_url is None:
        return {"resource_availability": "NO_RESOURCE_DECLARED"}
    resource = next(
        (
            candidate
            for candidate in package.resources
            if str(candidate.get("url") or "") == resource_url
        ),
        {},
    )
    return {
        "resource_availability": "UNVALIDATED_METADATA_ONLY",
        "resource_id": resource.get("id"),
        "resource_size_bytes": resource.get("size"),
        "resource_last_modified": resource.get("last_modified"),
    }


def catalog_resource_quality_issue(provenance: dict[str, Any]) -> tuple[str, str] | None:
    if provenance.get("resource_availability") == "NO_RESOURCE_DECLARED":
        return "CATALOG_RESOURCE_MISSING", "WARNING"
    resource_size = provenance.get("resource_size_bytes")
    if resource_size is not None:
        try:
            if int(resource_size) <= 1:
                return "CATALOG_RESOURCE_EMPTY", "WARNING"
        except (TypeError, ValueError):
            pass
    return None


async def _sync_catalog_quality_issue(
    conn: AsyncConnection,
    *,
    external_dataset_id: uuid.UUID,
    dataset_id: str,
    provenance: dict[str, Any],
) -> None:
    issue = catalog_resource_quality_issue(provenance)
    issue_codes = ("CATALOG_RESOURCE_MISSING", "CATALOG_RESOURCE_EMPTY")
    if issue is None:
        await conn.execute(
            data_quality_issues.update()
            .where(
                data_quality_issues.c.object_type == "EXTERNAL_DATASET",
                data_quality_issues.c.object_id == external_dataset_id,
                data_quality_issues.c.issue_code.in_(issue_codes),
                data_quality_issues.c.status.in_(("OPEN", "ACKNOWLEDGED")),
            )
            .values(status="RESOLVED", resolved_at=datetime.now(timezone.utc))
        )
        return
    issue_code, severity = issue
    await conn.execute(
        data_quality_issues.update()
        .where(
            data_quality_issues.c.object_type == "EXTERNAL_DATASET",
            data_quality_issues.c.object_id == external_dataset_id,
            data_quality_issues.c.issue_code.in_(issue_codes),
            data_quality_issues.c.issue_code != issue_code,
            data_quality_issues.c.status.in_(("OPEN", "ACKNOWLEDGED")),
        )
        .values(status="RESOLVED", resolved_at=datetime.now(timezone.utc))
    )
    existing_id = (
        await conn.execute(
            sa.select(data_quality_issues.c.id).where(
                data_quality_issues.c.object_type == "EXTERNAL_DATASET",
                data_quality_issues.c.object_id == external_dataset_id,
                data_quality_issues.c.issue_code == issue_code,
                data_quality_issues.c.status.in_(("OPEN", "ACKNOWLEDGED")),
            )
        )
    ).scalar()
    details = {"dataset_id": dataset_id, **provenance}
    if existing_id is None:
        await conn.execute(
            data_quality_issues.insert().values(
                id=uuid.uuid4(),
                object_type="EXTERNAL_DATASET",
                object_id=external_dataset_id,
                issue_code=issue_code,
                severity=severity,
                details=details,
            )
        )
    else:
        await conn.execute(
            data_quality_issues.update()
            .where(data_quality_issues.c.id == existing_id)
            .values(severity=severity, details=details, status="OPEN")
        )


async def _record_catalog_metadata(
    conn: AsyncConnection,
    *,
    raw_store: RawStore,
    package: PackageShowResponse,
) -> None:
    payload = json.dumps(
        package.raw_result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    raw_ref = await raw_store.put(
        source="ckan",
        resource="catalog_metadata",
        partition_key=f"dataset={package.catalog_dataset_id}",
        payload=payload,
    )
    exists = (
        await conn.execute(
            sa.select(source_records.c.id).where(
                source_records.c.source_system == "CKAN",
                source_records.c.resource_type == "CATALOG_METADATA",
                source_records.c.source_native_id == package.catalog_dataset_id,
                source_records.c.content_sha256 == raw_ref.content_sha256,
            )
        )
    ).scalar()
    if exists is not None:
        return
    await conn.execute(
        source_records.insert().values(
            id=uuid.uuid4(),
            source_system="CKAN",
            resource_type="CATALOG_METADATA",
            source_native_id=package.catalog_dataset_id,
            content_sha256=raw_ref.content_sha256,
            payload_uri=raw_ref.payload_uri,
            fetched_at=datetime.now(timezone.utc),
            http_status=200,
            license_code=package.license_code or "UNCONFIRMED",
            parse_status="PARSED",
        )
    )


async def refresh_curated_catalog(
    conn: AsyncConnection,
    *,
    raw_root: str = "./raw",
    now: datetime | None = None,
    interval: timedelta = CATALOG_REFRESH_INTERVAL,
    client: CkanClient | None = None,
) -> list[CatalogRefreshOutcome]:
    now = now or datetime.now(timezone.utc)
    own_client = client is None
    client = client or CkanClient(CkanConnectorConfig.from_env())
    raw_store = configured_raw_store(raw_root)
    outcomes: list[CatalogRefreshOutcome] = []
    try:
        for item in CURATED_SUPPLEMENTARY_DATASETS:
            existing = (
                await conn.execute(
                    sa.select(external_datasets.c.last_seen_at).where(
                        external_datasets.c.catalog_source == "DATA_GOV_GR",
                        sa.or_(
                            external_datasets.c.catalog_dataset_id == item.dataset_id,
                            external_datasets.c.config["dataset_id"].astext == item.dataset_id,
                        ),
                    )
                )
            ).scalar()
            if existing is not None and now - existing < interval:
                outcomes.append(CatalogRefreshOutcome(item.dataset_id, "NOT_DUE"))
                continue
            try:
                package = await client.package_show(item.dataset_id)
                resource_url, resource_type = choose_catalog_resource(package)
                resource_provenance = catalog_resource_provenance(package, resource_url)
                await _record_catalog_metadata(
                    conn,
                    raw_store=raw_store,
                    package=package,
                )
                registry_result = await upsert_external_dataset(
                    conn,
                    catalog_source="DATA_GOV_GR",
                    package=package,
                    resource_type=resource_type,
                    resource_url=resource_url,
                    update_frequency="CATALOG_DECLARED",
                    config={
                        **asdict(item),
                        **resource_provenance,
                        "catalog_metadata_modified": package.raw_result.get("metadata_modified"),
                        "catalog_checked_at": now.isoformat(),
                    },
                    ingestion_status="METADATA_ONLY",
                )
                await _sync_catalog_quality_issue(
                    conn,
                    external_dataset_id=registry_result.external_dataset_id,
                    dataset_id=item.dataset_id,
                    provenance=resource_provenance,
                )
                await conn.commit()
                outcomes.append(
                    CatalogRefreshOutcome(
                        item.dataset_id,
                        "METADATA_ONLY",
                        resource_url=resource_url,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one catalog record must not stop the sweep
                await conn.rollback()
                outcomes.append(
                    CatalogRefreshOutcome(
                        item.dataset_id,
                        "FAILED",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
    finally:
        if own_client:
            await client.aclose()
    return outcomes
