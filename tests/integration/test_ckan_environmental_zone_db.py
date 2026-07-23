"""CKAN environmental-layer ingestion (§22.2's "επιλεγμένα περιβαλλοντικά
επίπεδα") against a real Postgres+PostGIS instance.

Skipped automatically unless $DATABASE_URL is set. `boundary_type` is a
pure passthrough string in `ingest_boundaries_dataset` — nothing
special-cases `MUNICIPALITY` over any other value — so this test proves
that in practice rather than just asserting it via the CLI's `--boundary-
type` choices list: ingests the same GeoJSON fixture used for municipal
boundaries, but as `ENVIRONMENTAL_ZONE`, and confirms it lands correctly
with real PostGIS geometry, coexisting with a separately-scoped
`MUNICIPALITY` ingestion for the same `external_dataset_id` (the two
`boundary_type` values are independent replace-scopes, confirmed by
ingesting both and checking each still has its own row count).
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
import pytest

from packages.domain.tables import administrative_boundaries
from services.ingestion.connectors.ckan.db_writer import ingest_boundaries_dataset

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

GEOJSON_BYTES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "boundaries_sample.geojson"
).read_bytes()


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_environmental_zone_boundary_type_writes_real_geometry_independently():
    engine = create_async_engine(_asyncpg_url())
    external_dataset_id = uuid.uuid4()

    try:
        async with engine.connect() as conn:
            env_result = await ingest_boundaries_dataset(
                conn,
                external_dataset_id=external_dataset_id,
                boundary_type="ENVIRONMENTAL_ZONE",
                geojson_bytes=GEOJSON_BYTES,
                payload_uri="mem://env-zone",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert env_result.rows_written == 2

            municipality_result = await ingest_boundaries_dataset(
                conn,
                external_dataset_id=external_dataset_id,
                boundary_type="MUNICIPALITY",
                geojson_bytes=GEOJSON_BYTES,
                payload_uri="mem://municipality",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert municipality_result.rows_written == 2

            env_rows = (
                await conn.execute(
                    select(
                        administrative_boundaries.c.code,
                        func.ST_GeometryType(administrative_boundaries.c.geom).label("geom_type"),
                    ).where(
                        administrative_boundaries.c.external_dataset_id == external_dataset_id,
                        administrative_boundaries.c.boundary_type == "ENVIRONMENTAL_ZONE",
                    )
                )
            ).all()
            assert len(env_rows) == 2
            assert all(r.geom_type == "ST_MultiPolygon" for r in env_rows)

            municipality_rows = (
                await conn.execute(
                    select(administrative_boundaries.c.code).where(
                        administrative_boundaries.c.external_dataset_id == external_dataset_id,
                        administrative_boundaries.c.boundary_type == "MUNICIPALITY",
                    )
                )
            ).all()
            assert len(municipality_rows) == 2  # independent of the ENVIRONMENTAL_ZONE rows above, not merged/overwritten
    finally:
        await engine.dispose()
