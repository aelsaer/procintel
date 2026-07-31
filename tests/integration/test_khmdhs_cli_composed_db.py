"""The gap flagged after the process_id fix: no test previously exercised
`cli.py`'s fully-composed hook — adamChain + alerts + Διαύγεια + ΓΕΜΗ all
firing off the same ingestion event, the way a real
`backfill --with-diavgeia --with-gemi` run does. Each pairwise interaction
already has its own integration test (test_khmdhs_adamchain_db,
test_alerts_evaluate_db, test_diavgeia_resolve_db, test_gemi_resolve_db);
this one calls `_run_backfill` itself instead of calling each resolver
directly, so it actually exercises the composition in `cli.py`, not just
the pieces.

Skipped automatically unless $DATABASE_URL is set.
"""

import copy
import json
import os
import uuid
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    act_links,
    alert_events,
    alert_rules,
    entity_company_snapshots,
    procurement_acts,
    process_members,
    tenants,
    users,
)
from services.ingestion.connectors.khmdhs.adamchain import get_act_id_by_adam
from services.ingestion.connectors.khmdhs.cli import _run_backfill

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

KHMDHS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)
DECISION_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "diavgeia" / "decision_sample.json").read_text(
        encoding="utf-8"
    )
)
COMPANY_BODY = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "gemi" / "company_sample.json").read_text(
        encoding="utf-8"
    )
)

KHMDHS_BASE_URL = "https://khmdhs.example.test"
DIAVGEIA_BASE_URL = "https://diavgeia.example.test"
GEMI_BASE_URL = "https://gemi.example.test"
SEED_ADAM = "25SYMV012345678"
SUPPLIER_AFM = "090000045"


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


async def _seed_matching_alert_rule(engine) -> uuid.UUID:
    async with engine.connect() as conn:
        tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
        await conn.execute(tenants.insert().values(id=tenant_id, name="Composed Test Tenant"))
        await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))
        rule_id = uuid.uuid4()
        await conn.execute(
            alert_rules.insert().values(
                id=rule_id,
                tenant_id=tenant_id,
                user_id=user_id,
                name="composed-test cleaning-services rule",
                event_types=["contract.created", "contract.modified"],
                filters={"cpv_prefix": "9091"},
                schedule="IMMEDIATE",
                delivery_channels=["IN_APP"],
            )
        )
        await conn.commit()
        return rule_id


@respx.mock
async def test_full_backfill_with_diavgeia_and_gemi_fires_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("KHMDHS_API_BASE_URL", KHMDHS_BASE_URL)
    monkeypatch.setenv("DIAVGEIA_API_BASE_URL", DIAVGEIA_BASE_URL)
    monkeypatch.setenv("GEMI_API_BASE_URL", GEMI_BASE_URL)
    monkeypatch.setenv("GEMI_API_KEY", "test-key")

    unique_seed = uuid.uuid4().int
    supplier_afm = _valid_afm(unique_seed)
    seed_adam = f"25SYMV{unique_seed % 1_000_000_000:09d}"
    second_adam = f"25SYMV{(unique_seed + 1) % 1_000_000_000:09d}"
    gemi_number = 100_000_000_000 + unique_seed % 900_000_000_000
    khmdhs_fixture = copy.deepcopy(KHMDHS_FIXTURE)
    khmdhs_fixture["data"][0]["referenceNumber"] = seed_adam
    khmdhs_fixture["data"][1]["referenceNumber"] = second_adam
    for record in khmdhs_fixture["data"]:
        for awardee in record.get("awardees", []):
            awardee["vatNumber"] = supplier_afm
    company_body = copy.deepcopy(COMPANY_BODY)
    company_body["afm"] = supplier_afm
    company_body["arGemi"] = gemi_number

    respx.post(f"{KHMDHS_BASE_URL}/khmdhs-opendata/contract").mock(
        return_value=httpx.Response(200, json=khmdhs_fixture)
    )
    respx.get(url__regex=rf"{KHMDHS_BASE_URL}/khmdhs-opendata/adamChain/.*").mock(
        return_value=httpx.Response(200, json={"relatedRecords": []})
    )
    respx.get(url__regex=rf"{DIAVGEIA_BASE_URL}/decisions/.*").mock(
        return_value=httpx.Response(200, json=DECISION_BODY)
    )
    gemi_route = respx.get(
        f"{GEMI_BASE_URL}/companies", params={"afm": supplier_afm, "resultsSize": "1"}
    ).mock(
        return_value=httpx.Response(200, json={"searchResults": [company_body]})
    )
    respx.get(f"{GEMI_BASE_URL}/companies/{gemi_number}/documents").mock(
        return_value=httpx.Response(200, json={"decision": [], "publication": []})
    )

    engine = create_async_engine(_asyncpg_url())
    rule_id = await _seed_matching_alert_rule(engine)
    await engine.dispose()

    await _run_backfill(
        resources=["contract"],
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 30),
        database_url=DATABASE_URL,
        raw_root=str(tmp_path / "raw"),
        resolve_adam_chains=True,
        fire_alerts=True,
        with_diavgeia=True,
        with_gemi=True,
    )

    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            origin_act_id = await get_act_id_by_adam(conn, seed_adam)
            assert origin_act_id is not None

            # 1. adamChain assigned a process, and it's visible via the
            # denormalized pointer (the bug fixed last turn)
            origin_row = (
                await conn.execute(select(procurement_acts.c.process_id).where(procurement_acts.c.id == origin_act_id))
            ).scalar()
            assert origin_row is not None
            process_id = origin_row

            # 2. an alert fired for the matching rule
            events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events) >= 1
            assert events[0].event_type == "contract.created"

            # 3. a Διαύγεια decision act was created, linked to the origin
            # act, and joined to the same process — all through the CLI's
            # composed hook, not a direct resolve_decision_for_ada() call
            decision_links = (
                await conn.execute(select(act_links).where(act_links.c.to_act_id == origin_act_id))
            ).all()
            assert any(link.link_method == "EXACT_ADA" for link in decision_links)
            decision_act_id = next(link.from_act_id for link in decision_links if link.link_method == "EXACT_ADA")

            decision_process_id = (
                await conn.execute(
                    select(procurement_acts.c.process_id).where(procurement_acts.c.id == decision_act_id)
                )
            ).scalar()
            assert decision_process_id == process_id

            member_rows = (
                await conn.execute(select(process_members.c.act_id).where(process_members.c.process_id == process_id))
            ).all()
            assert origin_act_id in {m.act_id for m in member_rows}
            assert decision_act_id in {m.act_id for m in member_rows}

            # 4. a ΓΕΜΗ snapshot was written for the supplier entity — and the
            # cache gate meant only ONE real API call even though both
            # fixture records share the same supplier ΑΦΜ
            snapshot_rows = (await conn.execute(select(entity_company_snapshots))).all()
            assert any(row.vat_number == supplier_afm for row in snapshot_rows)
            assert gemi_route.call_count == 1
    finally:
        await engine.dispose()
