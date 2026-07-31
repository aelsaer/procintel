"""Load the full Greek NUTS 2024 hierarchy from Eurostat GISCO."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    administrative_boundaries,
    external_datasets,
    nuts_areas,
    source_records,
)
from packages.source_clients.raw_store import RawStore
from services.ingestion.connectors.ckan.geo import geojson_to_multipolygon_wkt


@dataclass(frozen=True)
class NutsLoadResult:
    rows_written: int
    content_sha256: str


def _parent_code(code: str, level: int) -> str | None:
    return code[:-1] if level > 0 else None


async def load_greece_nuts(
    conn: AsyncConnection,
    *,
    http_client: httpx.AsyncClient,
    raw_store: RawStore,
    url: str,
) -> NutsLoadResult:
    response = await http_client.get(url)
    response.raise_for_status()
    payload = response.content
    body = response.json()
    if body.get("type") != "FeatureCollection":
        raise ValueError("GISCO NUTS response is not a GeoJSON FeatureCollection")
    features = []
    for feature in body.get("features", []):
        properties = feature.get("properties") or {}
        code = str(properties.get("NUTS_ID") or "").strip().upper()
        level = properties.get("LEVL_CODE")
        if not code.startswith("EL") or not isinstance(level, int):
            continue
        features.append(
            {
                "code": code,
                "level": level,
                "name_el": str(properties.get("NUTS_NAME") or "").strip() or None,
                "name_en": str(properties.get("NAME_ENGL") or "").strip() or None,
                "parent_code": _parent_code(code, level),
                "wkt": geojson_to_multipolygon_wkt(feature.get("geometry") or {}),
            }
        )
    if not features or not any(item["level"] == 3 for item in features):
        raise ValueError("GISCO NUTS response did not contain the Greek 0-3 hierarchy")

    raw_ref = await raw_store.put(
        source="inspire",
        resource="gisco_nuts_2024",
        partition_key="country=EL",
        payload=payload,
    )
    existing = (
        await conn.execute(
            sa.select(source_records.c.id).where(
                source_records.c.source_system == "INSPIRE",
                source_records.c.resource_type == "NUTS_2024",
                source_records.c.content_sha256 == raw_ref.content_sha256,
            )
        )
    ).scalar()
    source_record_id = existing
    if source_record_id is None:
        source_record_id = uuid.uuid4()
        await conn.execute(
            source_records.insert().values(
                id=source_record_id,
                source_system="INSPIRE",
                resource_type="NUTS_2024",
                source_native_id="GISCO:NUTS:2024:EL",
                content_sha256=raw_ref.content_sha256,
                payload_uri=raw_ref.payload_uri,
                fetched_at=datetime.now(timezone.utc),
                http_status=response.status_code,
                license_code="EUROSTAT_GISCO_TERMS",
                parse_status="PARSED",
                attribution_text="European Commission, Eurostat/GISCO, NUTS 2024",
            )
        )

    now = datetime.now(timezone.utc)
    dataset_id = (
        await conn.execute(
            pg_insert(external_datasets)
            .values(
                id=uuid.uuid4(),
                catalog_source="INSPIRE",
                catalog_dataset_id="EUROSTAT_GISCO_NUTS_2024_EL",
                title="NUTS 2024 - Greece",
                publisher="European Commission, Eurostat/GISCO",
                license_code="EUROSTAT_GISCO_TERMS",
                resource_type="GeoJSON",
                resource_url=url,
                last_seen_at=now,
                ingestion_status="ONBOARDED",
                adapter_name="nuts_hierarchy",
                config={
                    "classification_version": "NUTS-2024",
                    "levels": [0, 1, 2, 3],
                    "country": "EL",
                    "content_sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
            .on_conflict_do_update(
                index_elements=[
                    external_datasets.c.catalog_source,
                    external_datasets.c.catalog_dataset_id,
                ],
                set_={
                    "resource_url": url,
                    "last_seen_at": now,
                    "ingestion_status": "ONBOARDED",
                    "config": {
                        "classification_version": "NUTS-2024",
                        "levels": [0, 1, 2, 3],
                        "country": "EL",
                        "content_sha256": hashlib.sha256(payload).hexdigest(),
                    },
                },
            )
            .returning(external_datasets.c.id)
        )
    ).scalar_one()

    for item in sorted(features, key=lambda value: value["level"]):
        await conn.execute(
            sa.text(
                """
                INSERT INTO nuts_areas (
                    code, level, name_el, name_en, classification_version,
                    parent_code, geom
                ) VALUES (
                    :code, :level, :name_el, :name_en, 'NUTS-2024',
                    :parent_code,
                    ST_Multi(ST_SetSRID(ST_GeomFromText(:wkt), 4326))
                )
                ON CONFLICT (code) DO UPDATE SET
                    level = EXCLUDED.level,
                    name_el = EXCLUDED.name_el,
                    name_en = EXCLUDED.name_en,
                    classification_version = EXCLUDED.classification_version,
                    parent_code = EXCLUDED.parent_code,
                    geom = EXCLUDED.geom
                """
            ),
            item,
        )

    await conn.execute(
        administrative_boundaries.delete().where(
            administrative_boundaries.c.external_dataset_id == dataset_id,
            administrative_boundaries.c.boundary_type.in_(
                ("REGION", "REGIONAL_UNIT")
            ),
        )
    )
    for item in features:
        boundary_type = {2: "REGION", 3: "REGIONAL_UNIT"}.get(item["level"])
        if boundary_type is None:
            continue
        await conn.execute(
            sa.text(
                """
                INSERT INTO administrative_boundaries (
                    id, boundary_type, external_dataset_id, code, name,
                    nuts_code, geom, source_record_id
                ) VALUES (
                    :id, :boundary_type, :external_dataset_id, :code, :name,
                    :nuts_code,
                    ST_Multi(ST_SetSRID(ST_GeomFromText(:wkt), 4326)),
                    :source_record_id
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "boundary_type": boundary_type,
                "external_dataset_id": str(dataset_id),
                "code": item["code"],
                "name": item["name_el"] or item["name_en"] or item["code"],
                "nuts_code": item["code"],
                "wkt": item["wkt"],
                "source_record_id": str(source_record_id),
            },
        )
    await conn.commit()
    return NutsLoadResult(
        rows_written=len(features),
        content_sha256=raw_ref.content_sha256,
    )
