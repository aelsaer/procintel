"""ΑΝΑΠΤΥΞΗ funding-link resolution against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Ingests one ΚΗΜΔΗΣ
contract (whose `publicFundingRefOps` is the MIS code in
tests/fixtures/anaptyxi/project_sample.json), resolves the funding link,
and verifies: `funding_projects`/`funding_links` are written with the
correct `link_method`/confidence/evidence (recording which candidate field
matched — the §19.4 "critical correction" made concrete), the beneficiary
entity is resolved by exact ΑΦΜ (reusing services/entity_resolution), and
re-resolving is idempotent (dedup on content hash, no duplicate link row).
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

from packages.domain.tables import entities, entity_identifiers, funding_links, funding_projects
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
CONTRACT_RECORD = KHMDHS_FIXTURE["data"][0]  # publicFundingRefOps = "OPS-0001"
PROJECT_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "anaptyxi" / "project_sample.json").read_text(
        encoding="utf-8"
    )
)
BASE_URL = "https://anaptyxi.example.test"
MIS = "OPS-0001"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_funding_link_resolved_with_beneficiary_and_is_idempotent(tmp_path):
    route = respx.get(
        f"{BASE_URL}/GetData.ashx",
        params={
            "queryType": "projectDetails",
            "queryArgument": MIS,
            "projectDetails": "all",
            "outputFormat": "json",
        },
    ).mock(return_value=httpx.Response(200, json=PROJECT_BODY))

    client = AnaptyxiClient(AnaptyxiConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000))
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
            act_id = ingest_result.act_upsert.act_id
            assert ("publicFundingRefOps", MIS) in ingest_result.act_upsert.funding_ref_candidates

            funding_project_id = await resolve_funding_link_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                act_id=act_id,
                mis_candidates=ingest_result.act_upsert.funding_ref_candidates,
            )
            assert funding_project_id is not None
            assert route.call_count == 1

            project_row = (
                await conn.execute(select(funding_projects).where(funding_projects.c.id == funding_project_id))
            ).one()
            assert project_row.mis_ops_code == MIS
            assert project_row.title == PROJECT_BODY["title"]
            assert project_row.program_period == "ANAPTYXI_2014_2020"

            beneficiary_row = (
                await conn.execute(
                    select(entities).where(entities.c.id == project_row.beneficiary_id)
                )
            ).one()
            beneficiary_afm = (
                await conn.execute(
                    select(entity_identifiers.c.value_normalized).where(
                        entity_identifiers.c.entity_id == beneficiary_row.id,
                        entity_identifiers.c.scheme == "AFM",
                    )
                )
            ).scalar()
            assert beneficiary_afm == "094259216"

            link_row = (
                await conn.execute(
                    select(funding_links).where(
                        funding_links.c.act_id == act_id, funding_links.c.funding_project_id == funding_project_id
                    )
                )
            ).one()
            assert link_row.link_method == "MIS_OPS_EXACT"
            assert float(link_row.confidence) == 0.95
            assert link_row.evidence == {"matched_field": "publicFundingRefOps", "mis_value": MIS}

            # re-resolving is idempotent: same project row updated in place,
            # no duplicate funding_links row, no second real API call needed
            # to determine that (dedup happens inside ingest_project_record)
            funding_project_id_again = await resolve_funding_link_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                act_id=act_id,
                mis_candidates=ingest_result.act_upsert.funding_ref_candidates,
            )
            assert funding_project_id_again == funding_project_id
            all_links = (
                await conn.execute(
                    select(funding_links).where(
                        funding_links.c.act_id == act_id,
                        funding_links.c.funding_project_id
                        == funding_project_id,
                    )
                )
            ).all()
            assert len(all_links) == 1
    finally:
        await client.aclose()
        await engine.dispose()
