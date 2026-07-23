"""Full pipeline test against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. This is the test the user
runs after `docker compose -f infra/docker/docker-compose.yml up -d` and
`./db/run_migrations.sh` — it is the actual end-to-end verification that
this environment could not perform (no Docker/Postgres available here).

    export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel
    pytest tests/integration
"""

import json
import os
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import act_cpv_codes, act_identifiers, act_parties, procurement_acts, source_records
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.pipeline import ingest_khmdhs_partition

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BASE_URL = "https://khmdhs.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_ingest_partition_writes_canonical_rows_and_is_idempotent(tmp_path):
    respx.post(f"{BASE_URL}/khmdhs-opendata/contract").mock(return_value=httpx.Response(200, json=SAMPLE_BODY))

    config = KhmdhsConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000)
    client = KhmdhsClient(config)
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            first_run = await ingest_khmdhs_partition(
                client=client, raw_store=raw_store, conn=conn, resource="contract",
                date_from=date(2025, 1, 1), date_to=date(2025, 1, 30),
            )
            assert first_run.records_seen == 2
            assert first_run.records_ingested == 2

            act_row = (
                await conn.execute(
                    select(procurement_acts).where(
                        procurement_acts.c.id.in_(
                            select(act_identifiers.c.act_id).where(
                                act_identifiers.c.scheme == "ADAM",
                                act_identifiers.c.value_normalized == "25SYMV012345678",
                            )
                        )
                    )
                )
            ).one()
            assert act_row.act_type == "CONTRACT"
            assert act_row.amount_gross == pytest.approx(124000.00)

            cpv_rows = (
                await conn.execute(select(act_cpv_codes).where(act_cpv_codes.c.act_id == act_row.id))
            ).all()
            assert {r.cpv_code for r in cpv_rows} == {"90911200", "90910000"}

            party_rows = (
                await conn.execute(select(act_parties).where(act_parties.c.act_id == act_row.id))
            ).all()
            assert {r.party_role for r in party_rows} == {"BUYER", "SUPPLIER"}

            # re-running the same partition must not create duplicate source_records
            # or duplicate acts — the content_sha256 dedup key makes this a no-op.
            second_run = await ingest_khmdhs_partition(
                client=client, raw_store=raw_store, conn=conn, resource="contract",
                date_from=date(2025, 1, 1), date_to=date(2025, 1, 30),
            )
            assert second_run.records_ingested == 0

            source_record_count = (
                await conn.execute(
                    select(source_records).where(
                        source_records.c.source_system == "KHMDHS",
                        source_records.c.source_native_id == "25SYMV012345678",
                    )
                )
            ).all()
            assert len(source_record_count) == 1
    finally:
        await client.aclose()
        await engine.dispose()
