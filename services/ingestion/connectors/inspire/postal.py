"""Load the official Greece postal-code to NUTS-3 correspondence."""

from __future__ import annotations

import csv
import io
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    external_datasets,
    postal_code_nuts,
    source_records,
)
from packages.source_clients.raw_store import RawStore


@dataclass(frozen=True)
class PostalNutsLoadResult:
    mappings_written: int
    postal_codes: int
    content_sha256: str


def _unquote(value: str | None) -> str:
    return (value or "").strip().strip("'").strip('"').strip()


def parse_postal_nuts_archive(payload: bytes) -> list[tuple[str, str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        csv_names = [
            name for name in archive.namelist() if name.lower().endswith(".csv")
        ]
        if len(csv_names) != 1:
            raise ValueError("postal archive must contain exactly one CSV file")
        text = archive.read(csv_names[0]).decode("utf-8-sig")
    rows: set[tuple[str, str]] = set()
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        postal_code = _unquote(row.get("CODE"))
        nuts_code = _unquote(row.get("NUTS3")).upper()
        if len(postal_code) == 5 and postal_code.isdigit() and nuts_code.startswith(
            "EL"
        ):
            rows.add((postal_code, nuts_code))
    if not rows:
        raise ValueError("postal archive did not contain valid Greek mappings")
    return sorted(rows)


async def load_greece_postal_nuts(
    conn: AsyncConnection,
    *,
    http_client: httpx.AsyncClient,
    raw_store: RawStore,
    url: str,
) -> PostalNutsLoadResult:
    response = await http_client.get(url)
    response.raise_for_status()
    payload = response.content
    mappings = parse_postal_nuts_archive(payload)
    postal_code_count = len({item[0] for item in mappings})
    raw_ref = await raw_store.put(
        source="inspire",
        resource="gisco_postal_nuts_2025",
        partition_key="country=EL",
        payload=payload,
    )
    source_record_id = (
        await conn.execute(
            sa.select(source_records.c.id).where(
                source_records.c.source_system == "INSPIRE",
                source_records.c.resource_type == "POSTAL_NUTS_2025",
                source_records.c.content_sha256 == raw_ref.content_sha256,
            )
        )
    ).scalar()
    if source_record_id is None:
        source_record_id = uuid.uuid4()
        await conn.execute(
            source_records.insert().values(
                id=source_record_id,
                source_system="INSPIRE",
                resource_type="POSTAL_NUTS_2025",
                source_native_id="GISCO:TERCET:PC2025:EL:NUTS-2024",
                content_sha256=raw_ref.content_sha256,
                payload_uri=raw_ref.payload_uri,
                fetched_at=datetime.now(timezone.utc),
                http_status=response.status_code,
                license_code="EUROSTAT_TERCET_TERMS",
                parse_status="PARSED",
                attribution_text=(
                    "European Commission, Eurostat/GISCO, "
                    "postal codes 2025 for NUTS 2024"
                ),
            )
        )

    await conn.execute(
        postal_code_nuts.delete().where(
            postal_code_nuts.c.country_code == "GR",
            postal_code_nuts.c.classification_version == "NUTS-2024",
        )
    )
    for postal_code, nuts_code in mappings:
        await conn.execute(
            postal_code_nuts.insert().values(
                country_code="GR",
                postal_code=postal_code,
                nuts_code=nuts_code,
                classification_version="NUTS-2024",
                source_record_id=source_record_id,
            )
        )

    now = datetime.now(timezone.utc)
    await conn.execute(
        pg_insert(external_datasets)
        .values(
            id=uuid.uuid4(),
            catalog_source="INSPIRE",
            catalog_dataset_id="EUROSTAT_TERCET_POSTAL_2025_EL",
            title="Postal codes 2025 for Greek NUTS 2024",
            publisher="European Commission, Eurostat/GISCO",
            license_code="EUROSTAT_TERCET_TERMS",
            resource_type="ZIP_CSV",
            resource_url=url,
            last_seen_at=now,
            ingestion_status="ONBOARDED",
            adapter_name="postal_nuts_lookup",
            config={
                "classification_version": "NUTS-2024",
                "country": "EL",
                "postal_codes": postal_code_count,
                "content_sha256": raw_ref.content_sha256,
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
                    "country": "EL",
                    "postal_codes": postal_code_count,
                    "content_sha256": raw_ref.content_sha256,
                },
            },
        )
    )
    await conn.commit()
    return PostalNutsLoadResult(
        mappings_written=len(mappings),
        postal_codes=postal_code_count,
        content_sha256=raw_ref.content_sha256,
    )
