"""CKAN administrative-boundaries ingestion against a real Postgres+PostGIS
instance.

Skipped automatically unless $DATABASE_URL is set. This is the one test in
the suite that actually exercises the GeoAlchemy2 integration end-to-end —
every other test that touches `administrative_boundaries.geom` is a unit
test on the pure GeoJSON-to-WKT conversion (`test_ckan_geo.py`), since that
part needs no DB. Onboards one CKAN dataset, downloads its GeoJSON
resource, and writes `administrative_boundaries` rows with real PostGIS
geometry. Verifies: the malformed feature (null geometry) in the fixture
is skipped; each written row's `geom` is genuinely a `ST_MultiPolygon` in
the database (not just a Python-side WKT string that never made it
through); re-ingesting the exact same file is a no-op (content-hash dedup);
ingesting a changed file replaces the rows wholesale rather than appending.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import administrative_boundaries, external_datasets
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.ckan.client import CkanClient
from services.ingestion.connectors.ckan.config import CkanConnectorConfig
from services.ingestion.connectors.ckan.db_writer import ingest_boundaries_dataset
from services.ingestion.connectors.ckan.registry import upsert_external_dataset

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

PACKAGE_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "package_show_sample.json").read_text(
        encoding="utf-8"
    )
)
GEOJSON_BYTES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "boundaries_sample.geojson"
).read_bytes()
BASE_URL = "https://data.gov.gr.example.test"
DATASET_ID = "plithysmos-dimon-2021"
RESOURCE_URL = PACKAGE_BODY["result"]["resources"][0]["url"]
BOUNDARY_TYPE = "MUNICIPALITY"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_boundaries_ingested_as_real_postgis_geometry_deduped_and_replaced(tmp_path):
    respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID}).mock(
        return_value=httpx.Response(200, json=PACKAGE_BODY)
    )
    resource_route = respx.get(RESOURCE_URL).mock(return_value=httpx.Response(200, content=GEOJSON_BYTES))

    client = CkanClient(CkanConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            # The fixture references EL301 and the production schema enforces
            # that NUTS codes exist. Seed the minimum reference row so this
            # integration test is reproducible on a genuinely empty database.
            await conn.execute(
                text(
                    """
                    INSERT INTO nuts_areas (code, level, name_el, name_en, classification_version)
                    VALUES ('EL301', 3, 'Βόρειος Τομέας Αθηνών', 'North Athens', 'NUTS-2021')
                    ON CONFLICT (code) DO NOTHING
                    """
                )
            )
            package = await client.package_show(DATASET_ID)
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source="DATA_GOV_GR",
                package=package,
                resource_type="GEOJSON",
                resource_url=RESOURCE_URL,
                adapter_name="boundaries",
                config={"boundary_type": BOUNDARY_TYPE},
            )
            await conn.commit()

            dataset_row = (
                await conn.execute(
                    select(external_datasets).where(external_datasets.c.id == registry_result.external_dataset_id)
                )
            ).one()
            assert dataset_row.adapter_name == "boundaries"

            resource_response = await client.fetch_resource_bytes(RESOURCE_URL)
            raw_ref = await raw_store.put(
                source="ckan",
                resource="administrative_boundary",
                partition_key=f"dataset={DATASET_ID}",
                payload=resource_response.content,
            )
            ingest_result = await ingest_boundaries_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                boundary_type=BOUNDARY_TYPE,
                geojson_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=resource_response.http_status,
                fetched_at=datetime.now(timezone.utc),
            )
            assert ingest_result.rows_written == 2  # the null-geometry feature is skipped
            assert resource_route.call_count == 1

            rows = (
                await conn.execute(
                    select(
                        administrative_boundaries.c.code,
                        administrative_boundaries.c.boundary_type,
                        func.ST_GeometryType(administrative_boundaries.c.geom).label("geom_type"),
                        func.ST_SRID(administrative_boundaries.c.geom).label("srid"),
                    ).where(administrative_boundaries.c.external_dataset_id == registry_result.external_dataset_id)
                )
            ).all()
            assert len(rows) == 2
            by_code = {r.code: r for r in rows}
            assert by_code["6101"].boundary_type == "MUNICIPALITY"
            assert by_code["6101"].geom_type == "ST_MultiPolygon"
            assert by_code["6101"].srid == 4326
            assert by_code["6102"].geom_type == "ST_MultiPolygon"

            # re-ingesting the exact same bytes (same content hash) is a no-op
            ingest_again = await ingest_boundaries_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                boundary_type=BOUNDARY_TYPE,
                geojson_bytes=resource_response.content,
                payload_uri=raw_ref.payload_uri,
                content_sha256=raw_ref.content_sha256,
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert ingest_again.source_record_id is None
            assert ingest_again.rows_written == 0
            rows_after_noop = (
                await conn.execute(
                    select(administrative_boundaries).where(
                        administrative_boundaries.c.external_dataset_id == registry_result.external_dataset_id
                    )
                )
            ).all()
            assert len(rows_after_noop) == 2

            # a changed file (different hash, only one usable feature) replaces the rows wholesale
            changed_geojson = json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"kallikratis_code": "6199", "name": "ΝΕΟΣ ΔΗΜΟΣ"},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[25.0, 39.0], [25.1, 39.0], [25.1, 39.1], [25.0, 39.1], [25.0, 39.0]]],
                            },
                        }
                    ],
                }
            ).encode("utf-8")
            changed_ref = await raw_store.put(
                source="ckan",
                resource="administrative_boundary",
                partition_key=f"dataset={DATASET_ID}",
                payload=changed_geojson,
            )
            ingest_changed = await ingest_boundaries_dataset(
                conn,
                external_dataset_id=registry_result.external_dataset_id,
                boundary_type=BOUNDARY_TYPE,
                geojson_bytes=changed_geojson,
                payload_uri=changed_ref.payload_uri,
                content_sha256=changed_ref.content_sha256,
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert ingest_changed.rows_written == 1
            rows_after_change = (
                await conn.execute(
                    select(administrative_boundaries.c.code).where(
                        administrative_boundaries.c.external_dataset_id == registry_result.external_dataset_id
                    )
                )
            ).all()
            assert [r.code for r in rows_after_change] == ["6199"]
    finally:
        await client.aclose()
        await engine.dispose()
