"""Alert event types beyond contract.* (§30.5) against a real Postgres
instance.

Skipped automatically unless $DATABASE_URL is set. Verifies
`evaluate_and_fire()`'s generalized `_EVENT_TYPES_BY_ACT_TYPE` mapping:
ingesting a ΚΗΜΔΗΣ `request` fires `opportunity.created` then
`opportunity.updated` on a material change (mirroring contract.*'s
created/modified pattern), ingesting a `payment` fires `payment.detected`
(a single event type either way, since §30.5 lists no
"payment.modified"), and an `auction` (AWARD act_type) ingestion fires
nothing at all — deliberately, since §30.5's list has no AWARD-mapped
event.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import alert_events, alert_rules, tenants, users
from services.alerts.delivery import LogDeliveryChannel
from services.alerts.evaluate import evaluate_and_fire
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs"
REQUEST_RECORD = json.loads((FIXTURES_DIR / "request_sample.json").read_text(encoding="utf-8"))["data"][0]
PAYMENT_RECORD = json.loads((FIXTURES_DIR / "payment_sample.json").read_text(encoding="utf-8"))["data"][0]
AUCTION_RECORD = json.loads((FIXTURES_DIR / "auction_sample.json").read_text(encoding="utf-8"))["data"][0]


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def _seed_tenant_and_rule(conn, *, event_types: list[str], cpv_prefix: str | None = None):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await conn.execute(tenants.insert().values(id=tenant_id, name="Test Tenant"))
    await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))
    rule_id = uuid.uuid4()
    await conn.execute(
        alert_rules.insert().values(
            id=rule_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name="test rule",
            event_types=event_types,
            filters={"cpv_prefix": cpv_prefix} if cpv_prefix else {},
            schedule="IMMEDIATE",
            delivery_channels=["IN_APP"],
        )
    )
    await conn.commit()
    return rule_id


async def test_opportunity_created_and_updated_from_request_resource():
    engine = create_async_engine(_asyncpg_url())
    delivery_channel = LogDeliveryChannel()

    try:
        async with engine.connect() as conn:
            rule_id = await _seed_tenant_and_rule(
                conn, event_types=["opportunity.created", "opportunity.updated"], cpv_prefix="3019"
            )

            result = await ingest_khmdhs_record(
                conn,
                resource="request",
                raw_record=REQUEST_RECORD,
                payload_uri="mem://request-1",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            fired = await evaluate_and_fire(conn, act_upsert=result.act_upsert, delivery_channel=delivery_channel)
            assert fired == 1

            events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events) == 1
            assert events[0].event_type == "opportunity.created"

            modified_record = dict(REQUEST_RECORD)
            modified_record["totalCostWithVAT"] = 15000.00
            modified_result = await ingest_khmdhs_record(
                conn,
                resource="request",
                raw_record=modified_record,
                payload_uri="mem://request-2",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            fired_modified = await evaluate_and_fire(
                conn, act_upsert=modified_result.act_upsert, delivery_channel=delivery_channel
            )
            assert fired_modified == 1
            all_events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert {e.event_type for e in all_events} == {"opportunity.created", "opportunity.updated"}
    finally:
        await engine.dispose()


async def test_payment_detected_from_payment_resource():
    engine = create_async_engine(_asyncpg_url())
    delivery_channel = LogDeliveryChannel()

    try:
        async with engine.connect() as conn:
            rule_id = await _seed_tenant_and_rule(conn, event_types=["payment.detected"])

            result = await ingest_khmdhs_record(
                conn,
                resource="payment",
                raw_record=PAYMENT_RECORD,
                payload_uri="mem://payment-1",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            fired = await evaluate_and_fire(conn, act_upsert=result.act_upsert, delivery_channel=delivery_channel)
            assert fired == 1

            events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events) == 1
            assert events[0].event_type == "payment.detected"
    finally:
        await engine.dispose()


async def test_auction_award_act_type_fires_no_event():
    engine = create_async_engine(_asyncpg_url())
    delivery_channel = LogDeliveryChannel()

    try:
        async with engine.connect() as conn:
            await _seed_tenant_and_rule(
                conn, event_types=["opportunity.created", "contract.created", "payment.detected"]
            )

            result = await ingest_khmdhs_record(
                conn,
                resource="auction",
                raw_record=AUCTION_RECORD,
                payload_uri="mem://auction-1",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert result.act_upsert.act_type == "AWARD"
            fired = await evaluate_and_fire(conn, act_upsert=result.act_upsert, delivery_channel=delivery_channel)
            assert fired == 0
    finally:
        await engine.dispose()
