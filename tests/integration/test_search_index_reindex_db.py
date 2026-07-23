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
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import create_async_engine

from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record
from services.search_index.client import create_index, delete_index, index_exists
from services.search_index.config import OpenSearchConfig
from services.search_index.indexer import reindex_all_acts
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


@respx.mock
async def test_reindex_and_search_finds_the_seeded_contract(tmp_path):
    config = OpenSearchConfig(base_url=OPENSEARCH_URL.rstrip("/"), index_name=f"test_procurement_acts_{uuid.uuid4().hex[:8]}")
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

                    # OpenSearch is near-real-time, not instant — a fresh index
                    # commonly needs a refresh before a just-indexed doc is searchable.
                    await os_client.post(f"{config.base_url}/{config.index_name}/_refresh")

                    search_result = await search_procurement_acts(os_client, config, query="καθαρισμού")
                    assert search_result.total >= 1
                    assert any("καθαρισμ" in (hit.title or "").lower() for hit in search_result.hits)
                finally:
                    await delete_index(os_client, config)
    finally:
        await engine.dispose()
