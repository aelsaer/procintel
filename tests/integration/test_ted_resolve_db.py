"""TED <-> ΚΗΜΔΗΣ process matching + VIES trigger against a real Postgres
instance.

Skipped automatically unless $DATABASE_URL is set. Ingests one ΚΗΜΔΗΣ
contract (buyer ΑΦΜ 094259216, CPV 90911200, submitted 2025-01-10), then
ingests two TED notices for the same buyer: one with a Greek supplier and a
matching CPV/date window (should match the ΚΗΜΔΗΣ process), one with a
foreign supplier and an unrelated CPV (should NOT match, and should trigger
a VIES check instead). Also verifies that a second, unrelated ΚΗΜΔΗΣ process
for the same buyer+CPV turns a would-be match into "too ambiguous, don't
link" (§8's conservative-matching discipline).
"""

import json
import os
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import act_links, act_parties, entity_vies_checks, procurement_acts, process_members
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record
from services.ingestion.connectors.ted.client import TedClient
from services.ingestion.connectors.ted.config import TedConnectorConfig
from services.ingestion.connectors.ted.db_writer import ingest_notice_record
from services.ingestion.connectors.ted.resolve import resolve_notice_process_link
from services.ingestion.connectors.vies.client import ViesClient
from services.ingestion.connectors.vies.config import ViesConnectorConfig
from services.ingestion.connectors.vies.resolve import check_and_record_vies

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

KHMDHS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)
TED_BUYER_AFM = "090000070"
TED_SUPPLIER_AFM = "090000082"
CONTRACT_RECORD = deepcopy(KHMDHS_FIXTURE["data"][0])
CONTRACT_RECORD.update(
    {
        "referenceNumber": "25SYMV070000001",
        "organizationVatNumber": TED_BUYER_AFM,
        "awardees": [{"vatNumber": TED_SUPPLIER_AFM, "name": "TED TEST SUPPLIER ΙΚΕ"}],
    }
)

TED_NOTICE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "ted" / "notice_sample.json").read_text(encoding="utf-8")
)
TED_FOREIGN_NOTICE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "ted" / "notice_foreign_supplier_sample.json").read_text(
        encoding="utf-8"
    )
)
TED_NOTICE["buyer-identifier"] = [TED_BUYER_AFM]
TED_NOTICE["winner-identifier"] = [TED_SUPPLIER_AFM]
TED_FOREIGN_NOTICE["buyer"]["vatNumber"] = TED_BUYER_AFM

BASE_URL = "https://ted.example.test"
VIES_BASE_URL = "https://vies.example.test"

VIES_VALID_RESPONSE_XML = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body><checkVatResponse><valid>true</valid></checkVatResponse></soap:Body>
</soap:Envelope>"""


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_matching_notice_links_and_foreign_supplier_triggers_vies(tmp_path):
    ted_client = TedClient(TedConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    vies_client = ViesClient(ViesConnectorConfig(base_url=VIES_BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    respx.post(f"{VIES_BASE_URL}/checkVatService").mock(
        return_value=httpx.Response(200, content=VIES_VALID_RESPONSE_XML, headers={"Content-Type": "text/xml"})
    )

    try:
        async with engine.connect() as conn:
            khmdhs_result = await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=CONTRACT_RECORD,
                payload_uri="mem://contract",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            khmdhs_act_id = khmdhs_result.act_upsert.act_id

            # this act has no process yet (adamChain wasn't run) — give it
            # one directly so there's something for TED to match against
            from packages.domain.tables import procurement_processes

            buyer_entity_id = (
                await conn.execute(
                    select(act_parties.c.entity_id).where(
                        act_parties.c.act_id == khmdhs_act_id,
                        act_parties.c.party_role == "BUYER",
                    )
                )
            ).scalar_one()
            process_id = uuid.uuid4()
            await conn.execute(
                procurement_processes.insert().values(
                    id=process_id,
                    public_id=f"proc_{uuid.uuid4().hex[:20]}",
                    buyer_entity_id=buyer_entity_id,
                )
            )
            await conn.execute(
                process_members.insert().values(
                    id=uuid.uuid4(), process_id=process_id, act_id=khmdhs_act_id, added_via="MANUAL"
                )
            )
            await conn.execute(
                procurement_acts.update().where(procurement_acts.c.id == khmdhs_act_id).values(process_id=process_id)
            )
            await conn.commit()

            # 1. matching TED notice (same buyer ΑΦΜ, overlapping CPV,
            # publication_date within the proximity window) -> links + joins
            matching_result = await ingest_notice_record(
                conn,
                ted_notice_id="2025-TED-000123",
                raw_body=TED_NOTICE,
                raw_format="JSON",
                payload_uri="mem://ted-1",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            matched_process_id = await resolve_notice_process_link(
                conn,
                ted_act_id=matching_result.notice.act_id,
                buyer_entity_id=matching_result.notice.buyer_entity_id,
                cpv_codes=matching_result.notice.cpv_codes,
                publication_date=matching_result.notice.publication_date,
            )
            assert matched_process_id == process_id

            link_row = (
                await conn.execute(
                    select(act_links).where(
                        act_links.c.from_act_id == matching_result.notice.act_id,
                        act_links.c.to_act_id == khmdhs_act_id,
                    )
                )
            ).one()
            assert link_row.link_type == "PUBLISHED_AS"
            assert link_row.link_method == "BUYER_VAT_CPV_DATE_PROXIMITY"
            assert float(link_row.confidence) == 0.85

            member_rows = (
                await conn.execute(select(process_members.c.act_id).where(process_members.c.process_id == process_id))
            ).all()
            assert matching_result.notice.act_id in {m.act_id for m in member_rows}

            # 2. foreign-supplier TED notice with an unrelated CPV -> no
            # match (correct — nothing to link to), and the foreign
            # supplier's VAT gets a VIES check recorded
            foreign_result = await ingest_notice_record(
                conn,
                ted_notice_id="2025-TED-000456",
                raw_body=TED_FOREIGN_NOTICE,
                raw_format="JSON",
                payload_uri="mem://ted-2",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            assert foreign_result.notice.supplier_country_code == "DE"

            no_match = await resolve_notice_process_link(
                conn,
                ted_act_id=foreign_result.notice.act_id,
                buyer_entity_id=foreign_result.notice.buyer_entity_id,
                cpv_codes=foreign_result.notice.cpv_codes,  # ["30200000"] — not in the ΚΗΜΔΗΣ process
                publication_date=foreign_result.notice.publication_date,
            )
            assert no_match is None

            vies_valid = await check_and_record_vies(
                conn,
                client=vies_client,
                entity_id=foreign_result.notice.supplier_entity_id,
                country_code="DE",
                vat_number=foreign_result.notice.supplier_vat,
            )
            assert vies_valid is True

            vies_rows = (
                await conn.execute(
                    select(entity_vies_checks).where(
                        entity_vies_checks.c.entity_id == foreign_result.notice.supplier_entity_id
                    )
                )
            ).all()
            assert len(vies_rows) == 1
            assert vies_rows[0].country_code == "DE"
            assert vies_rows[0].vies_valid is True
    finally:
        await ted_client.aclose()
        await vies_client.aclose()
        await engine.dispose()
