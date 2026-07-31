"""TED §21.3 Level 4 (buyer + title + amount + date, no CPV requirement)
against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Ingests one ΚΗΜΔΗΣ
contract (buyer ΑΦΜ 094259216, title "Παροχή υπηρεσιών καθαρισμού δημοσίων
κτιρίων", amount 124000.00), gives it a process directly, then ingests a
TED notice for the *same buyer* whose CPV code deliberately does not
overlap (so Level 3 finds nothing) but whose title and amount are close
enough to match at Level 4. Verifies: the link uses
`link_method='BUYER_TITLE_AMOUNT_DATE'` at a lower confidence than Level 3
(0.65, not 0.85), and is left unreviewed (`act_links.reviewed_by IS NULL`
— the review-queue signal, §21.3's "manual review" tier).
"""

import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import act_links, procurement_acts, process_members, procurement_processes
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record
from services.ingestion.connectors.ted.db_writer import ingest_notice_record
from services.ingestion.connectors.ted.resolve import resolve_notice_process_link

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

KHMDHS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)
CONTRACT_RECORD = KHMDHS_FIXTURE["data"][0]
TED_NOTICE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "ted" / "notice_level4_sample.json").read_text(
        encoding="utf-8"
    )
)


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _valid_afm(seed: int) -> str:
    prefix = f"{10_000_000 + seed % 89_999_999:08d}"
    checksum = (
        sum(int(prefix[index]) * (2 ** (8 - index)) for index in range(8))
        % 11
    ) % 10
    return f"{prefix}{checksum}"


async def test_level4_matches_on_title_amount_when_cpv_does_not_overlap(tmp_path):
    engine = create_async_engine(_asyncpg_url())
    unique_seed = uuid.uuid4().int
    buyer_afm = _valid_afm(unique_seed)
    contract_record = copy.deepcopy(CONTRACT_RECORD)
    contract_record["referenceNumber"] = (
        f"25SYMV{unique_seed % 1_000_000_000:09d}"
    )
    contract_record["organizationVatNumber"] = buyer_afm
    contract_record["organizationName"] = f"ΔΗΜΟΣ ΔΟΚΙΜΗΣ {unique_seed}"
    ted_notice = copy.deepcopy(TED_NOTICE)
    ted_notice_id = f"2025-TED-{unique_seed % 1_000_000_000:09d}"
    ted_notice["noticeId"] = ted_notice_id
    ted_notice["publicationNumber"] = (
        f"{unique_seed % 1_000_000:06d}-2025"
    )
    ted_notice["buyer"]["vatNumber"] = buyer_afm
    ted_notice["buyer"]["name"] = contract_record["organizationName"]

    try:
        async with engine.connect() as conn:
            khmdhs_result = await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=contract_record,
                payload_uri="mem://contract",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            khmdhs_act_id = khmdhs_result.act_upsert.act_id

            process_id = uuid.uuid4()
            await conn.execute(
                procurement_processes.insert().values(id=process_id, public_id=f"proc_{uuid.uuid4().hex[:20]}")
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

            notice_result = await ingest_notice_record(
                conn,
                ted_notice_id=ted_notice_id,
                raw_body=ted_notice,
                raw_format="JSON",
                payload_uri="mem://ted-level4",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            notice = notice_result.notice
            assert not set(notice.cpv_codes) & {"90911200", "90910000"}  # confirms Level 3 has nothing to match on

            matched_process_id = await resolve_notice_process_link(
                conn,
                ted_act_id=notice.act_id,
                buyer_entity_id=notice.buyer_entity_id,
                cpv_codes=notice.cpv_codes,
                publication_date=notice.publication_date,
                title=notice.title,
                amount=notice.amount,
            )
            assert matched_process_id == process_id

            link_row = (
                await conn.execute(
                    select(act_links).where(
                        act_links.c.from_act_id == notice.act_id, act_links.c.to_act_id == khmdhs_act_id
                    )
                )
            ).one()
            assert link_row.link_method == "BUYER_TITLE_AMOUNT_DATE"
            assert float(link_row.confidence) == 0.65
            assert float(link_row.confidence) < 0.85
            assert link_row.reviewed_by is None
            assert link_row.evidence["needs_review"] is True
    finally:
        await engine.dispose()
