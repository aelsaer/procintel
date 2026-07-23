"""adamChain + process grouping against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set — same pattern as
test_khmdhs_pipeline_db.py. Covers the three §16.6 cases: no existing
process (create one), one existing process (extend it), and two existing
processes revealed to be the same procurement (controlled merge with audit
trail, public_id of the merged-away process still resolvable via
merged_into_process_id).
"""

import os
import uuid
from datetime import date, datetime, timezone

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    act_links,
    process_members,
    process_merge_log,
    procurement_acts,
    procurement_processes,
    source_records,
)
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.khmdhs.adamchain import get_act_id_by_adam, resolve_adam_chain_for_act
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

BASE_URL = "https://khmdhs.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _minimal_record(adam: str) -> dict:
    return {
        "referenceNumber": adam,
        "title": f"Synthetic act {adam}",
        "submissionDate": "2025-01-10",
        "organizationVatNumber": "094259216",
    }


@respx.mock
async def test_zero_then_one_then_merge_process_assignment(tmp_path):
    request_adam = "25REQ000900001"
    contract_adam = "25SYMV000900002"

    respx.get(f"{BASE_URL}/khmdhs-opendata/adamChain/{request_adam}").mock(
        return_value=httpx.Response(200, json={"relatedRecords": []})
    )
    respx.get(f"{BASE_URL}/khmdhs-opendata/adamChain/{contract_adam}").mock(
        return_value=httpx.Response(
            200, json={"relatedRecords": [{"referenceNumber": request_adam}]}
        )
    )

    config = KhmdhsConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000)
    client = KhmdhsClient(config)
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            # 1. ingest the REQUEST act on its own -> zero existing processes -> new process
            await ingest_khmdhs_record(
                conn,
                resource="request",
                raw_record=_minimal_record(request_adam),
                payload_uri="mem://request",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            process_id_1 = await resolve_adam_chain_for_act(
                conn, client=client, raw_store=raw_store, seed_adam_normalized=request_adam
            )
            assert process_id_1 is not None

            # 2. ingest the CONTRACT act separately -> zero existing processes for
            # *it* -> gets its own new process, until its chain links it to the request.
            await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=_minimal_record(contract_adam),
                payload_uri="mem://contract",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()

            request_act_id = await get_act_id_by_adam(conn, request_adam)
            pre_merge_process = (
                await conn.execute(
                    select(process_members.c.process_id).where(process_members.c.act_id == request_act_id)
                )
            ).first()
            assert pre_merge_process.process_id == process_id_1

            # 3. adamChain for the contract reveals the request -> two existing
            # processes among {contract_act, request_act} -> controlled merge.
            process_id_2 = await resolve_adam_chain_for_act(
                conn, client=client, raw_store=raw_store, seed_adam_normalized=contract_adam
            )
            assert process_id_2 == process_id_1  # request's (earlier) process survives

            contract_act_id = await get_act_id_by_adam(conn, contract_adam)
            link_row = (
                await conn.execute(
                    select(act_links).where(
                        act_links.c.from_act_id == contract_act_id,
                        act_links.c.to_act_id == request_act_id,
                    )
                )
            ).one()
            assert link_row.link_method == "ADAMCHAIN"
            assert float(link_row.confidence) == 1.0

            members = (
                await conn.execute(
                    select(process_members.c.act_id).where(process_members.c.process_id == process_id_1)
                )
            ).all()
            assert {m.act_id for m in members} == {request_act_id, contract_act_id}

            # regression check: procurement_acts.process_id (the denormalized
            # pointer db/marts/procurement_360.sql actually reads) must be
            # kept in sync with process_members, for both the act that was
            # already a member before the merge (request_act_id, repointed)
            # and the one newly assigned (contract_act_id) — not just the
            # audit-trail table.
            act_process_ids = (
                await conn.execute(
                    select(procurement_acts.c.id, procurement_acts.c.process_id).where(
                        procurement_acts.c.id.in_([request_act_id, contract_act_id])
                    )
                )
            ).all()
            assert {row.process_id for row in act_process_ids} == {process_id_1}

            merge_log_rows = (
                await conn.execute(
                    select(process_merge_log).where(process_merge_log.c.surviving_process_id == process_id_1)
                )
            ).all()
            assert len(merge_log_rows) == 1
            merged_process_id = merge_log_rows[0].merged_process_id

            merged_process = (
                await conn.execute(
                    select(procurement_processes).where(procurement_processes.c.id == merged_process_id)
                )
            ).one()
            assert merged_process.record_status == "MERGED"
            assert merged_process.merged_into_process_id == process_id_1

            # re-running adamChain for the same ΑΔΑΜ is a no-op (content dedup)
            process_id_3 = await resolve_adam_chain_for_act(
                conn, client=client, raw_store=raw_store, seed_adam_normalized=contract_adam
            )
            assert process_id_3 == process_id_1

            adamchain_source_records = (
                await conn.execute(
                    select(source_records).where(
                        source_records.c.source_system == "KHMDHS",
                        source_records.c.resource_type == "adamChain",
                        source_records.c.source_native_id == contract_adam,
                    )
                )
            ).all()
            assert len(adamchain_source_records) == 1
    finally:
        await client.aclose()
        await engine.dispose()
