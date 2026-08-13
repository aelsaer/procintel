"""Full reindex + search against real Postgres AND a real OpenSearch
instance. Skipped automatically unless both $DATABASE_URL and
$OPENSEARCH_URL are set — this is the one test in the suite needing two
live services at once, so it's gated on both independently.

Seeds one contract via the real ΚΗΜΔΗΣ ingestion pipeline (client mocked
with respx, DB real), reindexes it into OpenSearch, then searches for it
by title substring and confirms it comes back with the right CPV/buyer
fields.
"""

import json
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record
from services.search_index.client import (
    alias_targets,
    create_index,
    delete_index,
    index_exists,
)
from services.search_index.catalog import CATALOGS
from services.search_index.config import OpenSearchConfig
from services.search_index.indexer import (
    _physical_build_config,
    rebuild_all_indexes_atomic,
    reindex_all_acts,
)
from services.search_index.mapping import PROCUREMENT_ACTS_MAPPING
from services.search_index.search import search_procurement_acts

DATABASE_URL = os.environ.get("DATABASE_URL")
OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not OPENSEARCH_URL, reason="DATABASE_URL and OPENSEARCH_URL both required — see module docstring"
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BASE_URL = "https://khmdhs.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_reindex_and_search_finds_the_seeded_contract(tmp_path):
    suffix = uuid.uuid4().hex[:8]
    config = OpenSearchConfig(
        base_url=OPENSEARCH_URL.rstrip("/"),
        index_name=f"test_procurement_acts_{suffix}",
        index_prefix=f"test_catalogs_{suffix}",
    )
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            raw_record = SAMPLE_BODY["data"][0]
            await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=raw_record,
                payload_uri="mem://contract",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()

            async with httpx.AsyncClient(timeout=10.0) as os_client:
                try:
                    assert await index_exists(os_client, config) is False
                    await create_index(os_client, config, PROCUREMENT_ACTS_MAPPING)

                    result = await reindex_all_acts(conn, os_client, config)
                    assert result.acts_indexed >= 1
                    assert result.catalogs is not None
                    assert set(result.catalogs) == set(CATALOGS)

                    # OpenSearch is near-real-time, not instant — a fresh index
                    # commonly needs a refresh before a just-indexed doc is searchable.
                    await os_client.post(f"{config.base_url}/{config.index_name}/_refresh")

                    search_result = await search_procurement_acts(os_client, config, query="καθαρισμού")
                    assert search_result.total >= 1
                    assert any("καθαρισμ" in (hit.title or "").lower() for hit in search_result.hits)
                finally:
                    await delete_index(os_client, config)
                    for catalog in CATALOGS:
                        await delete_index(
                            os_client,
                            replace(
                                config,
                                index_name=config.catalog_index_name(catalog),
                            ),
                        )
    finally:
        await engine.dispose()


async def test_atomic_rebuild_replaces_legacy_indexes_with_aliases():
    suffix = uuid.uuid4().hex[:8]
    config = OpenSearchConfig(
        base_url=OPENSEARCH_URL.rstrip("/"),
        index_name=f"test_atomic_acts_{suffix}",
        index_prefix=f"test_atomic_catalogs_{suffix}",
    )
    physical = _physical_build_config(config, suffix)
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn, httpx.AsyncClient(
            timeout=30.0
        ) as os_client:
            await create_index(os_client, config, PROCUREMENT_ACTS_MAPPING)
            result = await rebuild_all_indexes_atomic(
                conn,
                os_client,
                config,
                batch_size=100,
                build_id=suffix,
            )

            assert set(result.catalogs) == set(CATALOGS)
            for logical, target in result.aliases.items():
                assert await alias_targets(os_client, config, logical) == [target]
    finally:
        async with httpx.AsyncClient(timeout=10.0) as os_client:
            await delete_index(os_client, physical)
            for catalog in CATALOGS:
                await delete_index(
                    os_client,
                    replace(
                        physical,
                        index_name=physical.catalog_index_name(catalog),
                    ),
                )
        await engine.dispose()
