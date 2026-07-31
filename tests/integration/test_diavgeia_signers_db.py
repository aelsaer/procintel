"""Διαύγεια signer extraction (§6.3's name-only `PERSON` entity exception)
against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Resolves the same
decision fixture twice for two different origin acts (so the signer name
appears twice) and confirms: both signers become `PERSON` entities linked
via `act_parties(party_role='SIGNER_PERSON')`; the same name is deduped to
the same entity on the second resolution rather than creating a duplicate
person (weaker than ΑΦΜ-based identity, but not naive re-creation either);
re-ingesting the exact same decision is a no-op for `source_records` but
`_replace_signers` still runs — this is deliberately called every upsert,
even for a no-op-content-hash-dedup path where `ingest_decision_record`
returns early, so it's really only exercised on the ingest path here, not
on the dedup-skip path (see the module docstring in db_writer.py for why
a dedup-skip never re-derives signers at all).
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

from packages.domain.tables import act_parties, entities
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.resolve import resolve_decision_for_ada
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

KHMDHS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)
CONTRACT_RECORD = KHMDHS_FIXTURE["data"][0]
DECISION_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "diavgeia" / "decision_sample.json").read_text(
        encoding="utf-8"
    )
)
ADA = "7Α1Η465ΦΘΘ-ΘΙΚ"
DIAVGEIA_BASE_URL = "https://diavgeia.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


@respx.mock
async def test_signers_become_person_entities_and_dedup_by_name(tmp_path):
    respx.get(f"{DIAVGEIA_BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(200, json=DECISION_BODY))

    client = DiavgeiaClient(DiavgeiaConnectorConfig(base_url=DIAVGEIA_BASE_URL, rate_limit_per_minute=6000))
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

            decision_act_id = await resolve_decision_for_ada(
                conn, client=client, raw_store=raw_store, ada=ADA, origin_act_id=origin_act_id
            )
            assert decision_act_id is not None

            signer_rows = (
                await conn.execute(
                    select(entities.c.id, entities.c.canonical_name)
                    .select_from(entities.join(act_parties, act_parties.c.entity_id == entities.c.id))
                    .where(act_parties.c.act_id == decision_act_id, act_parties.c.party_role == "SIGNER_PERSON")
                )
            ).all()
            assert {r.canonical_name for r in signer_rows} == {"Ιωάννης Παπαδόπουλος", "Μαρία Γεωργίου"}
            for signer_row in signer_rows:
                entity_type = (
                    await conn.execute(
                        select(entities.c.entity_type).where(
                            entities.c.id == signer_row.id
                        )
                    )
                ).scalar()
                assert entity_type == "PERSON"

            # a second, unrelated decision naming one of the same signers
            # (by name) resolves to the *same* PERSON entity, not a new one
            second_ada = "9Ζ9Ζ999999-ΖΖΖ"
            second_body = dict(DECISION_BODY)
            second_body["signers"] = [{"name": "Ιωάννης Παπαδόπουλος"}]
            respx.get(f"{DIAVGEIA_BASE_URL}/decisions/{second_ada}").mock(
                return_value=httpx.Response(200, json=second_body)
            )
            second_decision_act_id = await resolve_decision_for_ada(
                conn, client=client, raw_store=raw_store, ada=second_ada, origin_act_id=origin_act_id
            )
            assert second_decision_act_id is not None
            assert second_decision_act_id != decision_act_id

            second_signer_ids = (
                await conn.execute(
                    select(act_parties.c.entity_id).where(
                        act_parties.c.act_id == second_decision_act_id,
                        act_parties.c.party_role == "SIGNER_PERSON",
                    )
                )
            ).all()
            same_name_entity_id = next(r.id for r in signer_rows if r.canonical_name == "Ιωάννης Παπαδόπουλος")
            assert {r.entity_id for r in second_signer_ids} == {same_name_entity_id}
    finally:
        await client.aclose()
        await engine.dispose()
