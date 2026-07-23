"""Διαύγεια §17.4 SEARCH-fallback linkage against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Ingests one ΚΗΜΔΗΣ
contract, then resolves via SEARCH — as if DIRECT_ADA_FETCH already found
nothing for its referenced ΑΔΑ (`resolve_decision_via_search` doesn't
retry direct fetch itself, so that precondition isn't re-exercised here;
see `test_diavgeia_resolve_db.py` for DIRECT_ADA_FETCH's own coverage) — a
decision under a previously-unreferenced ΑΔΑ whose organization label and
subject both plausibly match the origin act. Verifies: the decision is
linked with `link_method='DIAVGEIA_SEARCH_MATCH'` and `confidence < 1.0`
(§17.4, not `EXACT_ADA`/1.0); a weak organization match alone (title
similar, organization not) does not link; two equally-plausible candidates
is left unlinked too (ambiguous, per the "never guess on weak/ambiguous
signal" discipline used everywhere else in this codebase); and — the
ADVANCED_SEARCH disambiguation path — two ambiguous candidates *do* link
correctly when the caller supplies a `protocol_number` that only one of
them matches, via a follow-up `search_decisions_advanced` call.
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

from packages.domain.tables import act_links, procurement_acts
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.resolve import resolve_decision_via_search
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

KHMDHS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)
CONTRACT_RECORD = KHMDHS_FIXTURE["data"][0]
ORIGIN_TITLE = CONTRACT_RECORD["title"]  # "Παροχή υπηρεσιών καθαρισμού δημοσίων κτιρίων"
BUYER_NAME = "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"
DIAVGEIA_BASE_URL = "https://diavgeia.example.test"
SEARCH_MATCH_ADA = "9Ζ9Ζ999999-ΖΖΖ"

SEARCH_MATCH_BODY = {
    "subject": "Έγκριση διενέργειας διαγωνισμού καθαρισμού δημοσίων κτιρίων",
    "type": "ΑΝΑΘΕΣΗ ΕΡΓΩΝ / ΠΡΟΜΗΘΕΙΩΝ / ΥΠΗΡΕΣΙΩΝ / ΜΕΛΕΤΩΝ",
    "issueDate": "2025-01-09",
    "protocolNumber": "99999/2025",
    "organizationLabel": BUYER_NAME,
    "unitLabel": "ΤΜΗΜΑ ΠΡΟΜΗΘΕΙΩΝ",
    "documentUrl": "https://diavgeia.gov.gr/doc/search-match",
}


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def _seed_origin_act(conn) -> uuid.UUID:
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
    return ingest_result.act_upsert.act_id


@respx.mock
async def test_search_match_links_with_search_confidence_not_exact_ada(tmp_path):
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/search").mock(
        return_value=httpx.Response(200, json={"results": [SEARCH_MATCH_BODY | {"ada": SEARCH_MATCH_ADA}]})
    )
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/{SEARCH_MATCH_ADA}").mock(
        return_value=httpx.Response(200, json=SEARCH_MATCH_BODY)
    )

    client = DiavgeiaClient(DiavgeiaConnectorConfig(base_url=DIAVGEIA_BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            origin_act_id = await _seed_origin_act(conn)

            decision_act_id = await resolve_decision_via_search(
                conn,
                client=client,
                raw_store=raw_store,
                origin_act_id=origin_act_id,
                organization_query=BUYER_NAME,
                title_query=ORIGIN_TITLE,
            )
            assert decision_act_id is not None

            decision_row = (
                await conn.execute(select(procurement_acts).where(procurement_acts.c.id == decision_act_id))
            ).one()
            assert decision_row.act_type == "DIAVGEIA_DECISION"

            link_row = (
                await conn.execute(
                    select(act_links).where(
                        act_links.c.from_act_id == decision_act_id, act_links.c.to_act_id == origin_act_id
                    )
                )
            ).one()
            assert link_row.link_method == "DIAVGEIA_SEARCH_MATCH"
            assert float(link_row.confidence) < 1.0
            assert link_row.evidence["method"] == "search"
            assert "organization_similarity" in link_row.evidence
            assert "title_similarity" in link_row.evidence
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_weak_organization_match_does_not_link(tmp_path):
    mismatched_body = SEARCH_MATCH_BODY | {"organizationLabel": "ΤΕΛΕΙΩΣ ΑΣΧΕΤΟΣ ΦΟΡΕΑΣ", "ada": SEARCH_MATCH_ADA}
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/search").mock(
        return_value=httpx.Response(200, json={"results": [mismatched_body]})
    )

    client = DiavgeiaClient(DiavgeiaConnectorConfig(base_url=DIAVGEIA_BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            origin_act_id = await _seed_origin_act(conn)
            decision_act_id = await resolve_decision_via_search(
                conn,
                client=client,
                raw_store=raw_store,
                origin_act_id=origin_act_id,
                organization_query=BUYER_NAME,
                title_query=ORIGIN_TITLE,
            )
            assert decision_act_id is None
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_advanced_search_disambiguates_when_protocol_number_given(tmp_path):
    second_candidate = SEARCH_MATCH_BODY | {"ada": "8Y8Y888888-YYY", "protocolNumber": "88888/2025"}
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [SEARCH_MATCH_BODY | {"ada": SEARCH_MATCH_ADA}, second_candidate]},
        )
    )
    respx.get(
        f"{DIAVGEIA_BASE_URL}/decisions/search/advanced",
        params={
            "organization": BUYER_NAME,
            "q": ORIGIN_TITLE,
            "protocolNumber": SEARCH_MATCH_BODY["protocolNumber"],
        },
    ).mock(return_value=httpx.Response(200, json={"results": [SEARCH_MATCH_BODY | {"ada": SEARCH_MATCH_ADA}]}))
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/{SEARCH_MATCH_ADA}").mock(
        return_value=httpx.Response(200, json=SEARCH_MATCH_BODY)
    )

    client = DiavgeiaClient(DiavgeiaConnectorConfig(base_url=DIAVGEIA_BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            origin_act_id = await _seed_origin_act(conn)
            decision_act_id = await resolve_decision_via_search(
                conn,
                client=client,
                raw_store=raw_store,
                origin_act_id=origin_act_id,
                organization_query=BUYER_NAME,
                title_query=ORIGIN_TITLE,
                protocol_number=SEARCH_MATCH_BODY["protocolNumber"],
            )
            assert decision_act_id is not None

            link_row = (
                await conn.execute(
                    select(act_links).where(
                        act_links.c.from_act_id == decision_act_id, act_links.c.to_act_id == origin_act_id
                    )
                )
            ).one()
            assert link_row.link_method == "DIAVGEIA_SEARCH_MATCH"
    finally:
        await client.aclose()
        await engine.dispose()


@respx.mock
async def test_multiple_plausible_candidates_is_left_unlinked(tmp_path):
    second_candidate = SEARCH_MATCH_BODY | {"ada": "8Y8Y888888-YYY"}
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    SEARCH_MATCH_BODY | {"ada": SEARCH_MATCH_ADA},
                    second_candidate,
                ]
            },
        )
    )

    client = DiavgeiaClient(DiavgeiaConnectorConfig(base_url=DIAVGEIA_BASE_URL, rate_limit_per_minute=6000))
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            origin_act_id = await _seed_origin_act(conn)
            decision_act_id = await resolve_decision_via_search(
                conn,
                client=client,
                raw_store=raw_store,
                origin_act_id=origin_act_id,
                organization_query=BUYER_NAME,
                title_query=ORIGIN_TITLE,
            )
            assert decision_act_id is None
    finally:
        await client.aclose()
        await engine.dispose()
