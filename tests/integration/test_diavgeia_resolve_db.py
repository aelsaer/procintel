"""Διαύγεια resolution against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Ingests one ΚΗΜΔΗΣ
contract (whose `contractRelatedAda` is the ΑΔΑ in
tests/fixtures/diavgeia/decision_sample.json), resolves the Διαύγεια
decision for that ΑΔΑ, and verifies: the decision becomes its own
DIAVGEIA_DECISION act (not a corruption of the KHMDHS act — the bug this
connector's design specifically avoids, see
services/ingestion/connectors/khmdhs/db_writer.py's module docstring), it's
linked to the originating act (APPROVES/EXACT_ADA/confidence 1.0), it joins
the same process once adamChain has grouped the origin act, re-resolving is
a no-op (dedup), and a 404 (no decision for an ΑΔΑ) resolves to None without
raising.
"""

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

from packages.domain.tables import act_links, procurement_acts, process_members, source_records
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.resolve import resolve_decision_for_ada
from services.ingestion.connectors.khmdhs.adamchain import resolve_adam_chain_for_act
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

KHMDHS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)
CONTRACT_RECORD = KHMDHS_FIXTURE["data"][0]  # ADAM 25SYMV012345678, contractRelatedAda 7Α1Η465ΦΘΘ-ΘΙΚ
DECISION_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "diavgeia" / "decision_sample.json").read_text(
        encoding="utf-8"
    )
)
ADA = "7Α1Η465ΦΘΘ-ΘΙΚ"
KHMDHS_BASE_URL = "https://khmdhs.example.test"
DIAVGEIA_BASE_URL = "https://diavgeia.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_decision_resolved_linked_and_joined_to_process(tmp_path):
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(200, json=DECISION_BODY))
    respx.get(f"{KHMDHS_BASE_URL}/khmdhs-opendata/adamChain/25SYMV012345678").mock(
        return_value=httpx.Response(200, json={"relatedRecords": []})
    )

    diavgeia_client = DiavgeiaClient(DiavgeiaConnectorConfig(base_url=DIAVGEIA_BASE_URL, rate_limit_per_minute=6000))
    khmdhs_client = KhmdhsClient(KhmdhsConnectorConfig(base_url=KHMDHS_BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            ingest_result = await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=CONTRACT_RECORD,
                payload_uri="mem://contract",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            origin_act_id = ingest_result.act_upsert.act_id
            assert ADA in ingest_result.act_upsert.related_ada

            # the ΑΔΑ must NOT have been attached as an identifier of the
            # KHMDHS act itself (the modeling bug this design avoids)
            origin_row = (
                await conn.execute(select(procurement_acts).where(procurement_acts.c.id == origin_act_id))
            ).one()
            assert origin_row.act_type == "CONTRACT"
            assert origin_row.title == CONTRACT_RECORD["title"]  # not overwritten by the decision's subject

            process_id = await resolve_adam_chain_for_act(
                conn, client=khmdhs_client, raw_store=raw_store, seed_adam_normalized="25SYMV012345678"
            )
            assert process_id is not None

            decision_act_id = await resolve_decision_for_ada(
                conn, client=diavgeia_client, raw_store=raw_store, ada=ADA, origin_act_id=origin_act_id
            )
            assert decision_act_id is not None
            assert decision_act_id != origin_act_id

            decision_row = (
                await conn.execute(select(procurement_acts).where(procurement_acts.c.id == decision_act_id))
            ).one()
            assert decision_row.act_type == "DIAVGEIA_DECISION"
            assert decision_row.title == DECISION_BODY["subject"]

            # origin act's own title/type are untouched by the decision write
            origin_row_after = (
                await conn.execute(select(procurement_acts).where(procurement_acts.c.id == origin_act_id))
            ).one()
            assert origin_row_after.act_type == "CONTRACT"
            assert origin_row_after.title == CONTRACT_RECORD["title"]

            link_row = (
                await conn.execute(
                    select(act_links).where(
                        act_links.c.from_act_id == decision_act_id,
                        act_links.c.to_act_id == origin_act_id,
                    )
                )
            ).one()
            assert link_row.link_type == "APPROVES"
            assert link_row.link_method == "EXACT_ADA"
            assert float(link_row.confidence) == 1.0

            member_rows = (
                await conn.execute(select(process_members.c.act_id).where(process_members.c.process_id == process_id))
            ).all()
            assert {m.act_id for m in member_rows} == {origin_act_id, decision_act_id}

            # regression check: procurement_acts.process_id (the denormalized
            # pointer db/marts/procurement_360.sql actually reads) must be
            # set on the decision act too, not just process_members.
            decision_process_id = (
                await conn.execute(
                    select(procurement_acts.c.process_id).where(procurement_acts.c.id == decision_act_id)
                )
            ).scalar()
            assert decision_process_id == process_id

            # re-resolving is a no-op: same source_records row, no duplicate link
            decision_act_id_again = await resolve_decision_for_ada(
                conn, client=diavgeia_client, raw_store=raw_store, ada=ADA, origin_act_id=origin_act_id
            )
            assert decision_act_id_again == decision_act_id
            source_record_rows = (
                await conn.execute(
                    select(source_records).where(
                        source_records.c.source_system == "DIAVGEIA",
                        source_records.c.source_native_id == ADA,
                    )
                )
            ).all()
            assert len(source_record_rows) == 1
            link_rows_after = (
                await conn.execute(
                    select(act_links).where(
                        act_links.c.from_act_id == decision_act_id, act_links.c.to_act_id == origin_act_id
                    )
                )
            ).all()
            assert len(link_rows_after) == 1

            # an ΑΔΑ with no published decision resolves to None, not an error
            respx.get(f"{DIAVGEIA_BASE_URL}/decisions/NOPE-000000-XXX").mock(return_value=httpx.Response(404))
            missing = await resolve_decision_for_ada(
                conn,
                client=diavgeia_client,
                raw_store=raw_store,
                ada="NOPE-000000-XXX",
                origin_act_id=origin_act_id,
            )
            assert missing is None
    finally:
        await diavgeia_client.aclose()
        await khmdhs_client.aclose()
        await engine.dispose()
