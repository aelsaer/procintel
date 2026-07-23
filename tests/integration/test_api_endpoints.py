"""API endpoint tests against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set — same pattern as the
other integration tests. Seeds one contract via the real ingestion pipeline
(client mocked with respx, DB real) + adamChain resolution, then exercises
every endpoint through an in-process ASGI transport (no real HTTP server
needed).
"""

import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import act_parties, entities, entity_identifiers
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.khmdhs.adamchain import resolve_adam_chain_for_act
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.pipeline import ingest_khmdhs_partition
from services.competitors.participation import backfill_winner_participations, record_participation

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BASE_URL = "https://khmdhs.example.test"
SEED_ADAM = "25SYMV012345678"  # first record in contract_sample.json


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_api_endpoints_against_a_seeded_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    respx.post(f"{BASE_URL}/khmdhs-opendata/contract").mock(return_value=httpx.Response(200, json=SAMPLE_BODY))
    respx.get(f"{BASE_URL}/khmdhs-opendata/adamChain/{SEED_ADAM}").mock(
        return_value=httpx.Response(200, json={"relatedRecords": []})
    )
    # the fixture's second record also gets an adamChain call during ingestion
    respx.get(url__regex=rf"{BASE_URL}/khmdhs-opendata/adamChain/.*").mock(
        return_value=httpx.Response(200, json={"relatedRecords": []})
    )

    config = KhmdhsConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000)
    client = KhmdhsClient(config)
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    engine = create_async_engine(_asyncpg_url())

    async def _on_ingest_result(conn, resource, result) -> None:
        await resolve_adam_chain_for_act(
            conn, client=client, raw_store=raw_store, seed_adam_normalized=result.adam_normalized
        )

    process_id = None
    buyer_id = None
    supplier_id = None

    try:
        async with engine.connect() as conn:
            await ingest_khmdhs_partition(
                client=client,
                raw_store=raw_store,
                conn=conn,
                resource="contract",
                date_from=date(2025, 1, 1),
                date_to=date(2025, 1, 30),
                on_ingest_result=_on_ingest_result,
            )

            buyer_row = (
                await conn.execute(
                    select(entities.c.id)
                    .select_from(entities.join(entity_identifiers, entity_identifiers.c.entity_id == entities.c.id))
                    .where(entity_identifiers.c.scheme == "AFM", entity_identifiers.c.value_normalized == "094259216")
                )
            ).first()
            supplier_row = (
                await conn.execute(
                    select(entities.c.id)
                    .select_from(entities.join(entity_identifiers, entity_identifiers.c.entity_id == entities.c.id))
                    .where(entity_identifiers.c.scheme == "AFM", entity_identifiers.c.value_normalized == "090000045")
                )
            ).first()
            buyer_id = str(buyer_row.id)
            supplier_id = str(supplier_row.id)

            from services.ingestion.connectors.khmdhs.adamchain import get_act_id_by_adam
            from packages.domain.tables import procurement_acts

            act_id = await get_act_id_by_adam(conn, SEED_ADAM)
            act_row = (
                await conn.execute(
                    select(procurement_acts.c.process_id, procurement_acts.c.source_record_id).where(
                        procurement_acts.c.id == act_id
                    )
                )
            ).first()
            process_id = str(act_row.process_id)
            winners_seen, winners_inserted = await backfill_winner_participations(conn)
            assert winners_seen >= 1
            assert 0 <= winners_inserted <= winners_seen
            bidder_id = uuid.uuid4()
            await conn.execute(
                entities.insert().values(
                    id=bidder_id,
                    entity_type="COMPANY",
                    canonical_name="BETA BIDDER IKE",
                    normalized_name="BETA BIDDER IKE",
                    country_code="GR",
                )
            )
            assert await record_participation(
                conn,
                process_id=act_row.process_id,
                act_id=act_id,
                entity_id=bidder_id,
                participant_name="BETA BIDDER IKE",
                role="BIDDER",
                evidence_type="OFFICIAL_SOURCE",
                confidence=0.98,
                source_record_id=act_row.source_record_id,
                evidence={"test_fixture": "official bidder list"},
            )
            await conn.commit()
    finally:
        await client.aclose()
        await engine.dispose()

    from httpx import ASGITransport

    from apps.api.main import app

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
        contract_resp = await api_client.get(f"/v1/contracts/{SEED_ADAM}")
        assert contract_resp.status_code == 200
        contract_body = contract_resp.json()
        assert contract_body["amounts"]["gross"] == "124000.00"
        assert contract_body["buyer"]["vat"] == "094259216"
        assert contract_body["suppliers"][0]["vat"] == "090000045"
        assert "ADAM" in contract_body["identifiers"]
        assert contract_body["summary"]["methodology"] == "STRUCTURED_EXTRACTIVE"
        assert contract_body["official_records"][0]["official_url"].endswith(SEED_ADAM)
        assert contract_body["official_records"][0]["document_url"].endswith(
            f"/contract/attachment/{SEED_ADAM}"
        )

        process_resp = await api_client.get(f"/v1/processes/{process_id}")
        assert process_resp.status_code == 200
        assert process_resp.json()["process_id"] == process_id
        assert len(process_resp.json()["acts"]) >= 1
        assert process_resp.json()["summary"]["text"]
        assert process_resp.json()["official_records"][0]["identifier"] == SEED_ADAM

        timeline_resp = await api_client.get(f"/v1/processes/{process_id}/timeline")
        assert timeline_resp.status_code == 200
        assert any(n["act_id"] == str(act_id) for n in timeline_resp.json()["nodes"])

        search_by_adam = await api_client.get("/v1/search", params={"q": SEED_ADAM})
        assert search_by_adam.status_code == 200
        search_by_adam_hit = search_by_adam.json()["data"][0]
        assert search_by_adam_hit["match_type"] == "EXACT_IDENTIFIER"
        assert search_by_adam_hit["identifier_scheme"] == "ADAM"
        assert search_by_adam_hit["identifier_value"] == SEED_ADAM
        assert search_by_adam_hit["official_url"].endswith(SEED_ADAM)
        assert search_by_adam_hit["document_url"].endswith(
            f"/contract/attachment/{SEED_ADAM}"
        )

        search_by_title = await api_client.get("/v1/search", params={"q": "καθαρισμού"})
        assert search_by_title.status_code == 200
        assert len(search_by_title.json()["data"]) >= 1
        search_by_all_terms = await api_client.get(
            "/v1/search",
            params={"q": "παροχή υπηρεσιών καθαρισμού δημοσίων κτιρίων"},
        )
        assert search_by_all_terms.status_code == 200
        assert any(
            item["identifier_value"] == SEED_ADAM
            for item in search_by_all_terms.json()["data"]
        )

        buyer_resp = await api_client.get(f"/v1/buyers/{buyer_id}")
        assert buyer_resp.status_code == 200
        assert buyer_resp.json()["contract_count"] >= 1

        buyer_suppliers_resp = await api_client.get(f"/v1/buyers/{buyer_id}/suppliers")
        assert buyer_suppliers_resp.status_code == 200
        assert any(s["id"] == supplier_id for s in buyer_suppliers_resp.json()["suppliers"])

        company_resp = await api_client.get(f"/v1/companies/{supplier_id}")
        assert company_resp.status_code == 200
        assert company_resp.json()["contract_count"] >= 1

        company_contracts_resp = await api_client.get(f"/v1/companies/{supplier_id}/contracts")
        assert company_contracts_resp.status_code == 200
        assert any(c["id"] == str(act_id) for c in company_contracts_resp.json()["contracts"])

        competitors_resp = await api_client.get(
            "/v1/competitors/discover",
            params={
                "cpv_prefixes": "773,909",
                "keywords": "ανύπαρκτος όρος",
                "date_from": "2025-01-01",
                "date_to": "2025-01-31",
            },
        )
        assert competitors_resp.status_code == 200
        assert competitors_resp.json()["scope"]["cpv_prefixes"] == ["773", "909"]
        assert competitors_resp.json()["scope"]["keywords"] == ["ανύπαρκτος όρος"]
        assert competitors_resp.json()["scope"]["taxonomy_match"] == "ANY"
        competitor = next(item for item in competitors_resp.json()["competitors"] if item["company_id"] == supplier_id)
        assert competitor["classification"] == "CONFIRMED_WINNER"
        assert competitor["evidence_level"] == "OFFICIAL_AWARD"
        assert competitor["award_count"] >= 1
        assert "δραστηριότητα σε CPV 909" in competitor["score_evidence"]
        bidder = next(item for item in competitors_resp.json()["competitors"] if item["company_id"] == str(bidder_id))
        assert bidder["classification"] == "CONFIRMED_BIDDER"
        assert bidder["award_count"] == 0
        assert bidder["bid_count"] == 1
        assert "τεκμηριωμένη συμμετοχή" in bidder["score_evidence"]

        keyword_required_resp = await api_client.get(
            "/v1/competitors/discover",
            params={
                "cpv_prefixes": "72",
                "keywords": "καθαρισμός κτιρίων",
                "taxonomy_match": "KEYWORD_REQUIRED",
                "date_from": "2025-01-01",
                "date_to": "2025-01-31",
            },
        )
        assert keyword_required_resp.status_code == 200
        assert keyword_required_resp.json()["scope"]["taxonomy_match"] == "KEYWORD_REQUIRED"
        assert any(
            item["company_id"] == supplier_id
            for item in keyword_required_resp.json()["competitors"]
        )

        competitor_profile_resp = await api_client.get(f"/v1/competitors/{supplier_id}")
        assert competitor_profile_resp.status_code == 200
        competitor_profile = competitor_profile_resp.json()
        assert competitor_profile["metrics"]["award_count"] >= 1
        assert any(item["key"].startswith("909") for item in competitor_profile["cpv_distribution"])
        assert any(item["process_id"] == process_id for item in competitor_profile["recent_activity"])

        relationships_resp = await api_client.get(
            "/v1/intelligence/relationships",
            params={
                "cpv_prefixes": "773,909",
                "keywords": "ανύπαρκτος όρος",
                "date_from": "2025-01-01",
                "date_to": "2025-01-31",
            },
        )
        assert relationships_resp.status_code == 200
        assert any(item["process_id"] == process_id for item in relationships_resp.json()["table"])

        process_competition_resp = await api_client.get(f"/v1/processes/{process_id}/competition")
        assert process_competition_resp.status_code == 200
        process_competition = process_competition_resp.json()
        winner = next(item for item in process_competition["confirmed_participants"] if item["company_id"] == supplier_id)
        assert winner["classification"] == "CONFIRMED_WINNER"
        assert winner["confidence"] == 1.0
        assert "market inference" in process_competition["coverage_note"].lower()

        regions_resp = await api_client.get(
            "/v1/analytics/regions",
            params={"date_from": "2025-01-01", "date_to": "2025-01-31", "cpv_prefix": "909"},
        )
        assert regions_resp.status_code == 200
        attica = next(region for region in regions_resp.json() if region["nuts_code"] == "EL30")
        assert attica["region_name"] == "Αττική"
        assert attica["act_count"] >= 1
        assert attica["contract_count"] >= 1

        not_found_resp = await api_client.get("/v1/contracts/DOES-NOT-EXIST")
        assert not_found_resp.status_code == 404
