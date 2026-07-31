"""CKAN population-denominators ingestion against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Onboards one CKAN
dataset into `external_datasets` (idempotent upsert on `(catalog_source,
catalog_dataset_id)`), downloads its CSV resource, and writes population
`geo_denominators` rows. Verifies: the registry row is marked `ONBOARDED`
with the right `adapter_name`; the malformed rows in the fixture (no
geography code, no value) are skipped, not written; re-ingesting the exact
same file is a no-op (content-hash dedup, no duplicate rows); ingesting a
*changed* file for the same `(dataset, reference_year)` replaces the old
rows wholesale rather than appending to them.
"""

import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import external_datasets, geo_denominators
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.ckan.client import CkanClient
from services.ingestion.connectors.ckan.config import CkanConnectorConfig
from services.ingestion.connectors.ckan.db_writer import ingest_population_dataset
from services.ingestion.connectors.ckan.registry import upsert_external_dataset

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

PACKAGE_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "package_show_sample.json").read_text(
        encoding="utf-8"
    )
)
CSV_BYTES = (Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "population_sample.csv").read_bytes()
BASE_URL = "https://data.gov.gr.example.test"
DATASET_ID = "plithysmos-dimon-2021"
RESOURCE_URL = PACKAGE_BODY["result"]["resources"][0]["url"]


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_population_ingested_deduped_and_replaced_on_change(tmp_path):
    dataset_id = f"{DATASET_ID}-{uuid.uuid4().hex}"
    package_body = copy.deepcopy(PACKAGE_BODY)
    package_body["result"]["id"] = dataset_id
    package_body["result"]["name"] = dataset_id
    respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": dataset_id}).mock(
        return_value=httpx.Response(200, json=package_body)
    )
    resource_route = respx.get(RESOURCE_URL).mock(return_value=httpx.Response(200, content=CSV_BYTES))

    client = CkanClient(CkanConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            package = await client.package_show(dataset_id)
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source="DATA_GOV_GR",
                package=package,
                resource_type="CSV",
                resource_url=RESOURCE_URL,
                adapter_name="population",
                config={"reference_year": 2021},
            )
            await conn.commit()
            assert registry_result.is_new

            dataset_row = (
                await conn.execute(
                    select(external_datasets).where(external_datasets.c.id == registry_result.external_dataset_id)
                )
            ).one()
            assert dataset_row.ingestion_status == "ONBOARDED"
            assert dataset_row.adapter_name == "population"
            assert dataset_row.title == package_body["result"]["title"]

            resource_response = await client.fetch_resource_bytes(RESOURCE_URL)
            raw_ref = await raw_store.put(
                source="ckan", resource="population", partition_key=f"dataset={dataset_id}",
                payload=resource_response.content,
            )
            ingest_result = await ingest_population_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                reference_year=2021,
                csv_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=resource_response.http_status,
                fetched_at=datetime.now(timezone.utc),
            )
            assert ingest_result.rows_written == 2  # malformed rows skipped
            assert resource_route.call_count == 1

            rows = (
                await conn.execute(
                    select(geo_denominators).where(
                        geo_denominators.c.external_dataset_id == registry_result.external_dataset_id
                    )
                )
            ).all()
            assert len(rows) == 2
            by_code = {r.municipality_code: r for r in rows}
            assert float(by_code["6101"].value) == 643452.00
            assert by_code["6101"].metric_name == "POPULATION"
            assert by_code["6101"].reference_year == 2021

            # re-ingesting the exact same bytes (same content hash) is a no-op
            ingest_again = await ingest_population_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                reference_year=2021,
                csv_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert ingest_again.source_record_id is None
            assert ingest_again.rows_written == 0
            rows_after_noop = (
                await conn.execute(
                    select(geo_denominators).where(
                        geo_denominators.c.external_dataset_id == registry_result.external_dataset_id
                    )
                )
            ).all()
            assert len(rows_after_noop) == 2

            # a changed file (different hash) replaces the rows wholesale
            changed_csv = b"kallikratis_code,population\n6101,700000\n"
            changed_ref = await raw_store.put(
                source="ckan", resource="population", partition_key=f"dataset={dataset_id}", payload=changed_csv
            )
            ingest_changed = await ingest_population_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                reference_year=2021,
                csv_bytes=changed_csv,
                payload_uri=changed_ref.payload_uri,
                content_sha256=changed_ref.content_sha256,
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert ingest_changed.rows_written == 1
            rows_after_change = (
                await conn.execute(
                    select(geo_denominators).where(
                        geo_denominators.c.external_dataset_id == registry_result.external_dataset_id
                    )
                )
            ).all()
            assert len(rows_after_change) == 1
            assert float(rows_after_change[0].value) == 700000.00
    finally:
        await client.aclose()
        await engine.dispose()
