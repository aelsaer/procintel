"""CKAN school/hospital facility ingestion (§22.2's "βασικά στοιχεία
σχολείων/νοσοκομείων") against a real Postgres+PostGIS instance.

Skipped automatically unless $DATABASE_URL is set. Ingests two schools (one
with coordinates, one without) and confirms: the one with coordinates gets
a real `ST_Point` geometry at SRID 4326; the one without still gets a row
(name/capacity alone are still useful data, per the module docstring); a
changed file replaces the facility rows for that `(external_dataset_id,
facility_type)` scope wholesale, same idempotency rule as every other CKAN
adapter.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import facilities
from services.ingestion.connectors.ckan.db_writer import ingest_facilities_dataset

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

SCHOOLS_CSV = (
    b"code,name,lat,lon,students\n"
    b"SCH-001,1o Dimotiko Sxoleio,37.98,23.72,320\n"
    b"SCH-002,2o Dimotiko Sxoleio,,,150\n"
)


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_schools_ingested_with_and_without_geometry_and_replaced_on_change():
    engine = create_async_engine(_asyncpg_url())
    external_dataset_id = uuid.uuid4()

    try:
        async with engine.connect() as conn:
            result = await ingest_facilities_dataset(
                conn,
                external_dataset_id=external_dataset_id,
                facility_type="SCHOOL",
                capacity_metric="STUDENTS",
                csv_bytes=SCHOOLS_CSV,
                payload_uri="mem://schools",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
                capacity_field_candidates=("students",),
            )
            assert result.rows_written == 2

            rows = (
                await conn.execute(
                    select(
                        facilities.c.code,
                        facilities.c.capacity_value,
                        func.ST_SRID(facilities.c.geom).label("srid"),
                    ).where(facilities.c.external_dataset_id == external_dataset_id)
                )
            ).all()
            by_code = {r.code: r for r in rows}
            assert by_code["SCH-001"].capacity_value == 320
            assert by_code["SCH-001"].srid == 4326
            assert by_code["SCH-002"].srid is None  # no coordinates -> no geometry, still a real row

            # a changed file (different hash, one school) replaces wholesale
            changed_csv = b"code,name,students\nSCH-003,3o Dimotiko Sxoleio,200\n"
            changed_result = await ingest_facilities_dataset(
                conn,
                external_dataset_id=external_dataset_id,
                facility_type="SCHOOL",
                capacity_metric="STUDENTS",
                csv_bytes=changed_csv,
                payload_uri="mem://schools-v2",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
                capacity_field_candidates=("students",),
            )
            assert changed_result.rows_written == 1
            rows_after_change = (
                await conn.execute(
                    select(facilities.c.code).where(facilities.c.external_dataset_id == external_dataset_id)
                )
            ).all()
            assert [r.code for r in rows_after_change] == ["SCH-003"]
    finally:
        await engine.dispose()
