"""CKAN generic regional-indicator ingestion (§22.2's "περιφερειακοί
οικονομικοί δείκτες") against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Ingests a GDP-per-capita
CSV via `ingest_metric_dataset` (the generalized entry point behind
`ingest_population_dataset`), then ingests a population CSV for the *same*
`external_dataset_id`/`reference_year` — confirming the two metrics don't
collide: each gets its own `geo_denominators.metric_name`, and the
`source_records` dedup namespace is scoped per metric (`resource_type`) so
population and GDP data for the same dataset row don't fight over the same
content-hash dedup key.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import external_datasets, geo_denominators, source_records
from services.ingestion.connectors.ckan.db_writer import ingest_metric_dataset, ingest_population_dataset

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

GDP_CSV = b"nuts_code,gdpPerCapita\nEL301,25000.50\nEL303,18000.00\n"
POPULATION_CSV = b"nuts_code,population\nEL301,600000\n"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_regional_indicator_and_population_coexist_without_collision():
    engine = create_async_engine(_asyncpg_url())
    external_dataset_id = uuid.uuid4()

    try:
        async with engine.connect() as conn:
            await conn.execute(
                external_datasets.insert().values(
                    id=external_dataset_id,
                    catalog_source="TEST",
                    catalog_dataset_id=f"regional-metrics-{external_dataset_id}",
                    title="Regional metrics fixture",
                    ingestion_status="ONBOARDED",
                    adapter_name="metric",
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO nuts_areas (code, level, name_en, classification_version)
                    VALUES
                        ('EL301', 3, 'Central Athens', 'NUTS-2021'),
                        ('EL303', 3, 'West Athens', 'NUTS-2021')
                    ON CONFLICT (code) DO NOTHING
                    """
                )
            )
            gdp_result = await ingest_metric_dataset(
                conn,
                external_dataset_id=external_dataset_id,
                metric_name="GDP_PER_CAPITA",
                reference_year=2024,
                csv_bytes=GDP_CSV,
                payload_uri="mem://gdp",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
                value_field_candidates=("gdpPerCapita",),
                resource_type="geo_metric_gdp_per_capita",
            )
            assert gdp_result.rows_written == 2

            population_result = await ingest_population_dataset(
                conn,
                external_dataset_id=external_dataset_id,
                reference_year=2024,
                csv_bytes=POPULATION_CSV,
                payload_uri="mem://population",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert population_result.rows_written == 1

            rows = (
                await conn.execute(
                    select(geo_denominators.c.metric_name, geo_denominators.c.nuts_code, geo_denominators.c.value)
                    .where(geo_denominators.c.external_dataset_id == external_dataset_id)
                    .order_by(geo_denominators.c.metric_name, geo_denominators.c.nuts_code)
                )
            ).all()
            assert [(r.metric_name, r.nuts_code, str(r.value)) for r in rows] == [
                ("GDP_PER_CAPITA", "EL301", "25000.50"),
                ("GDP_PER_CAPITA", "EL303", "18000.00"),
                ("POPULATION", "EL301", "600000.00"),
            ]

            resource_types = (
                await conn.execute(
                    select(source_records.c.resource_type).where(
                        source_records.c.source_system == "CKAN",
                        source_records.c.source_native_id == str(external_dataset_id),
                    )
                )
            ).all()
            assert {r.resource_type for r in resource_types} == {"geo_metric_gdp_per_capita", "population"}
    finally:
        await engine.dispose()
