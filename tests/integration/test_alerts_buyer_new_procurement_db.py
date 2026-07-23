"""`buyer.new_procurement` (§30.5) against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. `test_khmdhs_adamchain_db.py`
already covers the three §16.6 process-assignment cases in detail; this
file adds the alert-firing layer `resolve_adam_chain_for_act`'s optional
`delivery_channel` parameter enables: a brand-new process for a buyer
fires `buyer.new_procurement` exactly once; a second act for the *same*
buyer that gets grouped into the *same already-existing* process (an
extension, via the chain link) does NOT fire again — only genuine process
creation counts, never an extension or a merge. Also confirms the
parameter is truly opt-in: omitting it (as every other existing caller
does) fires nothing, preserving old behavior exactly.
"""

import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import alert_events, alert_rules, tenants, users
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.alerts.delivery import LogDeliveryChannel
from services.ingestion.connectors.khmdhs.adamchain import resolve_adam_chain_for_act
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

BASE_URL = "https://khmdhs.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _minimal_record(adam: str) -> dict:
    return {
        "referenceNumber": adam,
        "title": f"Synthetic act {adam}",
        "submissionDate": "2025-01-10",
        "organizationVatNumber": "094259216",
    }


async def _seed_rule(conn) -> uuid.UUID:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await conn.execute(tenants.insert().values(id=tenant_id, name="Test Tenant"))
    await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))
    rule_id = uuid.uuid4()
    await conn.execute(
        alert_rules.insert().values(
            id=rule_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name="new procurements",
            event_types=["buyer.new_procurement"],
            filters={},
            schedule="IMMEDIATE",
            delivery_channels=["IN_APP"],
        )
    )
    await conn.commit()
    return rule_id


@respx.mock
async def test_new_process_fires_once_extension_does_not_and_opt_out_fires_nothing(tmp_path):
    request_adam = "25REQ000900101"
    contract_adam = "25SYMV000900102"

    respx.get(f"{BASE_URL}/khmdhs-opendata/adamChain/{request_adam}").mock(
        return_value=httpx.Response(200, json={"relatedRecords": []})
    )
    respx.get(f"{BASE_URL}/khmdhs-opendata/adamChain/{contract_adam}").mock(
        return_value=httpx.Response(200, json={"relatedRecords": [{"referenceNumber": request_adam}]})
    )

    config = KhmdhsConnectorConfig(base_url=BASE_URL, rate_limit_per_minute=6000)
    client = KhmdhsClient(config)
    raw_store = LocalFilesystemRawStore(tmp_path / "raw")
    delivery_channel = LogDeliveryChannel()
    engine = create_async_engine(_asyncpg_url())

    try:
        async with engine.connect() as conn:
            rule_id = await _seed_rule(conn)

            # 1. brand-new process for this buyer -> fires exactly once
            await ingest_khmdhs_record(
                conn,
                resource="request",
                raw_record=_minimal_record(request_adam),
                payload_uri="mem://request",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            process_id_1 = await resolve_adam_chain_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                seed_adam_normalized=request_adam,
                delivery_channel=delivery_channel,
            )
            assert process_id_1 is not None

            events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events) == 1
            assert events[0].event_type == "buyer.new_procurement"

            # 2. a second act for the same buyer, whose chain links it to the
            # request above -> extends the *existing* process, not new -> no fire
            await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=_minimal_record(contract_adam),
                payload_uri="mem://contract",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            process_id_2 = await resolve_adam_chain_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                seed_adam_normalized=contract_adam,
                delivery_channel=delivery_channel,
            )
            assert process_id_2 == process_id_1  # same process, extended — not new

            events_after_extension = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events_after_extension) == 1  # unchanged — extension, not a new process

            # 3. omitting delivery_channel entirely (every pre-existing caller's
            # behavior) must not raise and must not fire anything retroactively
            third_adam = "25REQ000900103"
            respx.get(f"{BASE_URL}/khmdhs-opendata/adamChain/{third_adam}").mock(
                return_value=httpx.Response(200, json={"relatedRecords": []})
            )
            await ingest_khmdhs_record(
                conn,
                resource="request",
                raw_record=_minimal_record(third_adam),
                payload_uri="mem://third",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            await conn.commit()
            process_id_3 = await resolve_adam_chain_for_act(
                conn, client=client, raw_store=raw_store, seed_adam_normalized=third_adam
            )
            assert process_id_3 is not None
            assert process_id_3 != process_id_1  # a genuinely new, unrelated process

            events_after_opt_out = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events_after_opt_out) == 1  # still just the one from step 1 — opt-out respected
    finally:
        await client.aclose()
        await engine.dispose()
