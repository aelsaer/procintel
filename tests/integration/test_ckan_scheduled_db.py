"""`refresh_due_ckan_datasets` against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Onboards one CKAN
dataset (mirroring `test_ckan_population_db.py`'s fixture) and confirms:
a freshly-onboarded dataset (just synced, `last_seen_at` just set) is
*not* due; forcing `last_seen_at` back beyond the refresh interval makes
it due, and a due sweep re-syncs it (via the real `_sync_population`,
against a mocked CKAN HTTP endpoint) and advances `last_seen_at` again; a
dataset held by another session's advisory lock is skipped, not blocked.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import external_datasets
from packages.source_clients.pg_lock import try_advisory_lock
from services.ingestion.connectors.ckan.registry import upsert_external_dataset
from services.ingestion.connectors.ckan.scheduled import refresh_due_ckan_datasets

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
async def test_freshly_onboarded_dataset_is_not_due(tmp_path):
    from services.ingestion.connectors.ckan.client import CkanClient
    from services.ingestion.connectors.ckan.config import CkanConnectorConfig

    respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID}).mock(
        return_value=httpx.Response(200, json=PACKAGE_BODY)
    )
    respx.get(RESOURCE_URL).mock(return_value=httpx.Response(200, content=CSV_BYTES))

    client = CkanClient(CkanConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            package = await client.package_show(DATASET_ID)
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

            outcomes = await refresh_due_ckan_datasets(conn, database_url=DATABASE_URL, raw_root=str(tmp_path / "raw"))
            mine = [o for o in outcomes if o.external_dataset_id == registry_result.external_dataset_id]
            assert len(mine) == 1
            assert mine[0].ran is False
            assert mine[0].skipped_reason == "not due"
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_stale_dataset_is_refreshed_and_watermark_advances(tmp_path, monkeypatch):
    from services.ingestion.connectors.ckan.client import CkanClient
    from services.ingestion.connectors.ckan.config import CkanConnectorConfig

    package_route = respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID}).mock(
        return_value=httpx.Response(200, json=PACKAGE_BODY)
    )
    respx.get(RESOURCE_URL).mock(return_value=httpx.Response(200, content=CSV_BYTES))

    client = CkanClient(CkanConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    monkeypatch.setenv("CKAN_API_BASE_URL", BASE_URL)
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            package = await client.package_show(DATASET_ID)
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source="DATA_GOV_GR",
                package=package,
                resource_type="CSV",
                resource_url=RESOURCE_URL,
                adapter_name="population",
                config={"reference_year": 2021},
            )
            stale_last_seen_at = datetime.now(timezone.utc) - timedelta(days=30)
            await conn.execute(
                external_datasets.update()
                .where(external_datasets.c.id == registry_result.external_dataset_id)
                .values(last_seen_at=stale_last_seen_at)
            )
            await conn.commit()

            outcomes = await refresh_due_ckan_datasets(
                conn,
                database_url=os.environ["DATABASE_URL"],
                raw_root=str(tmp_path / "raw"),
            )
            mine = [o for o in outcomes if o.external_dataset_id == registry_result.external_dataset_id]
            assert len(mine) == 1
            assert mine[0].ran is True
            assert mine[0].error is None
            assert package_route.call_count == 2  # once for onboarding, once for the due refresh

            refreshed_row = (
                await conn.execute(
                    select(external_datasets).where(external_datasets.c.id == registry_result.external_dataset_id)
                )
            ).one()
            assert refreshed_row.last_seen_at > stale_last_seen_at
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_dataset_locked_by_another_session_is_skipped(tmp_path):
    from services.ingestion.connectors.ckan.client import CkanClient
    from services.ingestion.connectors.ckan.config import CkanConnectorConfig

    respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID}).mock(
        return_value=httpx.Response(200, json=PACKAGE_BODY)
    )
    respx.get(RESOURCE_URL).mock(return_value=httpx.Response(200, content=CSV_BYTES))

    client = CkanClient(CkanConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    engine = create_async_engine(_asyncpg_url())
    lock_engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn, lock_engine.connect() as lock_conn:
            package = await client.package_show(DATASET_ID)
            registry_result = await upsert_external_dataset(
                conn,
                catalog_source="DATA_GOV_GR",
                package=package,
                resource_type="CSV",
                resource_url=RESOURCE_URL,
                adapter_name="population",
                config={"reference_year": 2021},
            )
            stale_last_seen_at = datetime.now(timezone.utc) - timedelta(days=30)
            await conn.execute(
                external_datasets.update()
                .where(external_datasets.c.id == registry_result.external_dataset_id)
                .values(last_seen_at=stale_last_seen_at)
            )
            await conn.commit()

            held = await try_advisory_lock(lock_conn, f"procintel:orchestration:CKAN:{DATASET_ID}")
            assert held

            outcomes = await refresh_due_ckan_datasets(conn, database_url=DATABASE_URL, raw_root=str(tmp_path / "raw"))
            mine = [o for o in outcomes if o.external_dataset_id == registry_result.external_dataset_id]
            assert len(mine) == 1
            assert mine[0].ran is False
            assert mine[0].skipped_reason == "locked by another scheduler"
    finally:
        await client.aclose()
        await engine.dispose()
        await lock_engine.dispose()
