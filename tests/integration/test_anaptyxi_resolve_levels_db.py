"""ΑΝΑΠΤΥΞΗ join-hierarchy Levels 2-4 (§19.2) against a real Postgres
instance.

Skipped automatically unless $DATABASE_URL is set. Each test ingests the
same ΚΗΜΔΗΣ contract fixture (Level 1's `misCode` lookup mocked as a 404,
forcing fallthrough), then mocks a beneficiary-ΑΦΜ search response shaped
to land on a specific level:

- Level 2: a candidate whose title is similar and whose period overlaps
  the act's own submission date -> `AFM_TITLE_PERIOD`, confidence 0.85.
- Level 3: a candidate whose title is dissimilar (Level 2 would reject it)
  but whose raw metadata contains one of the act's own referenced ΑΔΑ
  values -> `ADA_ADAM_IN_METADATA`, confidence 0.90.
- Level 4: a candidate with no period fields at all (Level 2 rejects for
  missing period, not title) and no ΑΔΑ match, but within Level 4's
  looser title+amount tolerance -> `FUZZY_TITLE_AMOUNT_REGION`, confidence
  0.60, and — the point of this tier — `funding_links.reviewed_by IS NULL`
  (pending review; §19.2's "mandatory review when confidence isn't high").
"""

import json
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import funding_links
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.anaptyxi.client import AnaptyxiClient
from services.ingestion.connectors.anaptyxi.config import AnaptyxiConnectorConfig
from services.ingestion.connectors.anaptyxi.resolve import resolve_funding_link_for_act
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

KHMDHS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)
CONTRACT_RECORD = KHMDHS_FIXTURE["data"][0]  # ADAM 25SYMV012345678, buyer AFM 094259216, submissionDate 2025-01-10
BASE_URL = "https://anaptyxi.example.test"
BUYER_AFM = "094259216"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def _seed_origin_act(conn):
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
    return ingest_result.act_upsert


@respx.mock
async def test_level2_afm_title_period_match(tmp_path):
    respx.get(f"{BASE_URL}/projects", params={"misCode": "OPS-0001"}).mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/projects/search", params={"beneficiaryVatNumber": BUYER_AFM}).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "misCode": "OPS-LEVEL2",
                        "title": "Παροχή υπηρεσιών καθαρισμού δημοσίων κτιρίων - Έργο ΕΣΠΑ",
                        "startDate": "2024-06-01",
                        "endDate": "2026-06-01",
                        "budget": 124000.00,
                    }
                ]
            },
        )
    )

    client = AnaptyxiClient(AnaptyxiConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            act_upsert = await _seed_origin_act(conn)
            funding_project_id = await resolve_funding_link_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                act_id=act_upsert.act_id,
                mis_candidates=act_upsert.funding_ref_candidates,
                beneficiary_afm=BUYER_AFM,
                act_title=CONTRACT_RECORD["title"],
                act_date=date(2025, 1, 10),
            )
            assert funding_project_id is not None

            link_row = (
                await conn.execute(
                    select(funding_links).where(
                        funding_links.c.act_id == act_upsert.act_id,
                        funding_links.c.funding_project_id == funding_project_id,
                    )
                )
            ).one()
            assert link_row.link_method == "AFM_TITLE_PERIOD"
            assert float(link_row.confidence) == 0.85
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_level3_ada_in_metadata_match(tmp_path):
    respx.get(f"{BASE_URL}/projects", params={"misCode": "OPS-0001"}).mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/projects/search", params={"beneficiaryVatNumber": BUYER_AFM}).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "misCode": "OPS-LEVEL3",
                        "title": "Εντελώς Άσχετος Τίτλος Έργου Αποχέτευσης",
                        "relatedAda": "6Ω0Ζ465ΦΘΘ-ΔΕΖ",
                    }
                ]
            },
        )
    )

    client = AnaptyxiClient(AnaptyxiConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            act_upsert = await _seed_origin_act(conn)
            assert "6Ω0Ζ465ΦΘΘ-ΔΕΖ" in act_upsert.related_ada

            funding_project_id = await resolve_funding_link_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                act_id=act_upsert.act_id,
                mis_candidates=act_upsert.funding_ref_candidates,
                beneficiary_afm=BUYER_AFM,
                act_title=CONTRACT_RECORD["title"],
                related_ada_candidates=act_upsert.related_ada,
            )
            assert funding_project_id is not None

            link_row = (
                await conn.execute(
                    select(funding_links).where(
                        funding_links.c.act_id == act_upsert.act_id,
                        funding_links.c.funding_project_id == funding_project_id,
                    )
                )
            ).one()
            assert link_row.link_method == "ADA_ADAM_IN_METADATA"
            assert float(link_row.confidence) == 0.90
            assert link_row.evidence["matched_ada"] == "6Ω0Ζ465ΦΘΘ-ΔΕΖ"
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_level4_fuzzy_match_is_flagged_for_review(tmp_path):
    respx.get(f"{BASE_URL}/projects", params={"misCode": "OPS-0001"}).mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/projects/search", params={"beneficiaryVatNumber": BUYER_AFM}).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "misCode": "OPS-LEVEL4",
                        "title": "Καθαρισμός Δημοτικών Κτιρίων",  # no startDate/endDate at all -> Level 2 rejects on missing period, not title
                        "budget": 120000.00,  # within 15% of 124000.00
                    }
                ]
            },
        )
    )

    client = AnaptyxiClient(AnaptyxiConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            act_upsert = await _seed_origin_act(conn)
            funding_project_id = await resolve_funding_link_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                act_id=act_upsert.act_id,
                mis_candidates=act_upsert.funding_ref_candidates,
                beneficiary_afm=BUYER_AFM,
                act_title=CONTRACT_RECORD["title"],
                act_date=date(2099, 1, 1),  # far outside any period -> Level 2 rejects
                act_amount=Decimal("124000.00"),
            )
            assert funding_project_id is not None

            link_row = (
                await conn.execute(
                    select(funding_links).where(
                        funding_links.c.act_id == act_upsert.act_id,
                        funding_links.c.funding_project_id == funding_project_id,
                    )
                )
            ).one()
            assert link_row.link_method == "FUZZY_TITLE_AMOUNT_REGION"
            assert float(link_row.confidence) == 0.60
            assert link_row.reviewed_by is None
            assert link_row.evidence["needs_review"] is True
    finally:
        await client.aclose()
        await engine.dispose()
