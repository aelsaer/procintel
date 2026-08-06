"""External-dataset registry writes (`external_datasets`, §22.1).

Upserts by the DB's own unique key `(catalog_source, catalog_dataset_id)` —
one row per onboarded CKAN dataset, refreshed (not duplicated) every time
its `package_show` metadata is re-fetched. This registry is a catalogue,
not an operational store: it records *that* a dataset exists and is
onboarded, with which adapter handles it — the actual denominator/reference
data an adapter produces (e.g. `geo_denominators`) is written separately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import external_datasets

from .client import PackageShowResponse


@dataclass(frozen=True)
class ExternalDatasetUpsertResult:
    external_dataset_id: uuid.UUID
    is_new: bool


async def upsert_external_dataset(
    conn: AsyncConnection,
    *,
    catalog_source: str,
    package: PackageShowResponse,
    resource_type: str | None = None,
    resource_url: str | None = None,
    update_frequency: str | None = None,
    adapter_name: str | None = None,
    config: dict[str, Any] | None = None,
    ingestion_status: str | None = None,
) -> ExternalDatasetUpsertResult:
    existing = (
        await conn.execute(
            select(external_datasets.c.id).where(
                external_datasets.c.catalog_source == catalog_source,
                external_datasets.c.catalog_dataset_id == package.catalog_dataset_id,
            )
        )
    ).first()
    is_new = existing is None
    dataset_id = existing.id if existing is not None else uuid.uuid4()

    values: dict[str, Any] = dict(
        title=package.title,
        publisher=package.publisher,
        license_code=package.license_code,
        resource_type=resource_type,
        resource_url=resource_url,
        update_frequency=update_frequency,
        last_seen_at=datetime.now(timezone.utc),
    )
    if adapter_name is not None:
        values["adapter_name"] = adapter_name
        values["ingestion_status"] = "ONBOARDED"
    if ingestion_status is not None:
        values["ingestion_status"] = ingestion_status
    if config is not None:
        values["config"] = config

    if is_new:
        await conn.execute(
            external_datasets.insert().values(
                id=dataset_id,
                catalog_source=catalog_source,
                catalog_dataset_id=package.catalog_dataset_id,
                **values,
            )
        )
    else:
        await conn.execute(external_datasets.update().where(external_datasets.c.id == dataset_id).values(**values))

    return ExternalDatasetUpsertResult(external_dataset_id=dataset_id, is_new=is_new)
