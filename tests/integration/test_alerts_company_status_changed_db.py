"""`company.status_changed` (§30.5) against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. `test_gemi_resolve_db.py`
already covers the snapshot-transition mechanics in detail; this file adds
the alert-firing layer on top: the first-ever snapshot for a company
(`old_status is None`) must NOT fire `company.status_changed` (there's
nothing to report a change *from*), but a later transition where the
status itself actually changes must fire exactly once, and a change to
some *other* field (with the status staying the same) must not fire it at
all.
"""

import copy
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import alert_events, alert_rules, entities, entity_identifiers, tenants, users
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.alerts.delivery import LogDeliveryChannel
from services.alerts.evaluate import evaluate_company_status_change_and_fire
from services.ingestion.connectors.gemi.client import GemiClient
from services.ingestion.connectors.gemi.config import GemiConnectorConfig
from services.ingestion.connectors.gemi.provider import GemiCompanyRegistryProvider
from services.ingestion.connectors.gemi.resolve import resolve_company_snapshot

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gemi" / "company_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BASE_URL = "https://gemi.example.test"
AFM = "090000069"
AR_GEMI = 123456789069
SAMPLE_BODY.update({"afm": AFM, "arGemi": AR_GEMI})


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


async def _make_company_entity(conn, afm: str) -> uuid.UUID:
    entity_id = uuid.uuid4()
    await conn.execute(
        entities.insert().values(id=entity_id, entity_type="COMPANY", canonical_name=afm, normalized_name=afm)
    )
    await conn.execute(
        entity_identifiers.insert().values(
            id=uuid.uuid4(),
            entity_id=entity_id,
            scheme="AFM",
            value_raw=afm,
            value_normalized=afm,
            country_code="GR",
        )
    )
    await conn.commit()
    return entity_id


async def _seed_rule(conn, entity_id: uuid.UUID) -> uuid.UUID:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await conn.execute(tenants.insert().values(id=tenant_id, name="Test Tenant"))
    await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))
    rule_id = uuid.uuid4()
    await conn.execute(
        alert_rules.insert().values(
            id=rule_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name="company status changes",
            event_types=["company.status_changed"],
            filters={"supplier_id": str(entity_id)},
            schedule="IMMEDIATE",
            delivery_channels=["IN_APP"],
        )
    )
    await conn.commit()
    return rule_id


@respx.mock
async def test_status_change_fires_once_not_on_first_snapshot_or_unrelated_change(tmp_path):
    unique_seed = uuid.uuid4().int
    afm = _valid_afm(unique_seed)
    ar_gemi = 100_000_000_000 + unique_seed % 900_000_000_000
    sample_body = copy.deepcopy(SAMPLE_BODY)
    sample_body["afm"] = afm
    sample_body["arGemi"] = ar_gemi
    client = GemiClient(GemiConnectorConfig(base_url=BASE_URL, api_key="test-key", rate_limit_per_minute=6000))
    provider = GemiCompanyRegistryProvider(client)
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    delivery_channel = LogDeliveryChannel()
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            entity_id = await _make_company_entity(conn, afm)
            rule_id = await _seed_rule(conn, entity_id)

            # 1. first-ever snapshot: wrote_new_snapshot=True but old_status is
            # None (nothing to report a change from) -> must not fire
            respx.get(
                f"{BASE_URL}/companies", params={"afm": afm, "resultsSize": "1"}
            ).mock(
                return_value=httpx.Response(200, json={"searchResults": [sample_body]})
            )
            respx.get(f"{BASE_URL}/companies/{ar_gemi}/documents").mock(
                return_value=httpx.Response(200, json={"decision": [], "publication": []})
            )
            first = await resolve_company_snapshot(
                conn, provider=provider, raw_store=raw_store, afm_normalized=afm, entity_id=entity_id
            )
            assert first.wrote_new_snapshot is True
            assert first.old_status is None
            fired_first = await evaluate_company_status_change_and_fire(
                conn,
                entity_id=entity_id,
                old_status=first.old_status,
                new_status=first.new_status,
                delivery_channel=delivery_channel,
            )
            assert fired_first == 0

            # 2. a real status transition -> fires exactly once
            fired_change = await evaluate_company_status_change_and_fire(
                conn,
                entity_id=entity_id,
                old_status="ACTIVE",
                new_status="IN_LIQUIDATION",
                delivery_channel=delivery_channel,
            )
            assert fired_change == 1

            events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events) == 1
            assert events[0].event_type == "company.status_changed"
            assert events[0].payload["old_status"] == "ACTIVE"
            assert events[0].payload["new_status"] == "IN_LIQUIDATION"

            # 3. status unchanged (some other field changed instead) -> not a status change, no fire
            fired_unchanged = await evaluate_company_status_change_and_fire(
                conn,
                entity_id=entity_id,
                old_status="IN_LIQUIDATION",
                new_status="IN_LIQUIDATION",
                delivery_channel=delivery_channel,
            )
            assert fired_unchanged == 0
            events_after = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events_after) == 1  # unchanged from step 2
    finally:
        await client.aclose()
        await engine.dispose()
