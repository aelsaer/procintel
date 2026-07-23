"""ΜΕΦ expense linkage against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Ingests one ΚΗΜΔΗΣ
contract, resolves its Διαύγεια decision (so the ΑΔΑ->origin-act path
`resolve.py._find_origin_act_for_ada` depends on actually exists), then
resolves ΜΕΦ expenses for the contract's awardee ΑΦΜ and verifies:
Tier 1 (same ΑΔΑ + same ΑΦΜ) links the expense to the ΚΗΜΔΗΣ act at
confidence 0.99 — not to the Διαύγεια decision act, which carries no
act_parties; a non-matching expense (no ΑΔΑ, no amount/date match) is
stored with `linked_act_id` left NULL (Tier 4, candidate-only per §20.2);
and re-resolving is idempotent (dedup on content hash, no duplicate
mef_expenses rows, link untouched).
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

from packages.domain.tables import mef_expenses, mef_organizations
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.resolve import resolve_decision_for_ada
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record
from services.ingestion.connectors.mef.client import MefClient
from services.ingestion.connectors.mef.config import MefConnectorConfig
from services.ingestion.connectors.mef.resolve import resolve_expenses_for_contractor

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
EXPENSES_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "mef" / "expenses_sample.json").read_text(
        encoding="utf-8"
    )
)
ADA = "7Α1Η465ΦΘΘ-ΘΙΚ"
CONTRACTOR_AFM = "090000045"
DIAVGEIA_BASE_URL = "https://diavgeia.example.test"
MEF_BASE_URL = "https://mef.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_tier1_link_and_tier4_candidate_and_idempotent(tmp_path):
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(200, json=DECISION_BODY))
    expenses_route = respx.get(
        f"{MEF_BASE_URL}/api/spendings",
        params={"searchTerm": CONTRACTOR_AFM, "limit": "200", "offset": "0"},
    ).mock(
        return_value=httpx.Response(200, json=EXPENSES_BODY)
    )

    diavgeia_client = DiavgeiaClient(DiavgeiaConnectorConfig(base_url=DIAVGEIA_BASE_URL, rate_limit_per_minute=6000))
    mef_client = MefClient(MefConnectorConfig(base_url=MEF_BASE_URL, rate_limit_per_minute=6000))
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
            contractor_entity_id = ingest_result.act_upsert.contractor_entity_id
            assert contractor_entity_id is not None

            decision_act_id = await resolve_decision_for_ada(
                conn, client=diavgeia_client, raw_store=raw_store, ada=ADA, origin_act_id=origin_act_id
            )
            assert decision_act_id is not None

            ingested_count = await resolve_expenses_for_contractor(
                conn,
                client=mef_client,
                raw_store=raw_store,
                contractor_entity_id=contractor_entity_id,
                afm_normalized=CONTRACTOR_AFM,
            )
            assert ingested_count == 2
            assert expenses_route.call_count == 1

            expense_rows = (
                await conn.execute(
                    select(mef_expenses).order_by(mef_expenses.c.amount.desc())
                )
            ).all()
            assert len(expense_rows) == 2

            tier1_row, tier4_row = expense_rows
            assert tier1_row.linked_act_id == origin_act_id
            assert tier1_row.linked_act_id != decision_act_id
            assert tier1_row.link_method == "ADA_AND_AFM"
            assert float(tier1_row.confidence) == 0.99
            assert tier1_row.recipient_entity_id == contractor_entity_id

            assert tier4_row.linked_act_id is None
            assert tier4_row.link_method is None
            assert tier4_row.confidence is None
            assert tier4_row.recipient_entity_id == contractor_entity_id

            org_row = (
                await conn.execute(
                    select(mef_organizations).where(mef_organizations.c.id == tier1_row.mef_organization_id)
                )
            ).one()
            assert org_row.afm_raw == "094259216"

            # re-resolving is idempotent: dedup on content hash, no duplicate rows
            ingested_again = await resolve_expenses_for_contractor(
                conn,
                client=mef_client,
                raw_store=raw_store,
                contractor_entity_id=contractor_entity_id,
                afm_normalized=CONTRACTOR_AFM,
            )
            assert ingested_again == 0
            all_rows_again = (await conn.execute(select(mef_expenses))).all()
            assert len(all_rows_again) == 2
    finally:
        await diavgeia_client.aclose()
        await mef_client.aclose()
        await engine.dispose()
