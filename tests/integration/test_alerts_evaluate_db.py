"""Alert evaluation against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Seeds one contract via
the real ingestion pipeline, an active matching alert_rule, and a
non-matching one, then verifies: the matching rule fires exactly once on
insert (contract.created), fires again on a material amount change
(contract.modified), does NOT fire twice for the identical re-ingestion
(dedup via alert_events' unique index), and the non-matching rule never
fires.
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

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
SEED_RECORD = SAMPLE_BODY["data"][0]  # ADAM 25SYMV012345678, CPV 90911200/90910000, gross 124000.00
BASE_URL = "https://khmdhs.example.test"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_alert_fires_on_create_and_modify_and_dedups():
    engine = create_async_engine(_asyncpg_url())
    delivery_channel = LogDeliveryChannel()
    seed_record = dict(SEED_RECORD)
    seed_record["referenceNumber"] = f"25SYMV{uuid.uuid4().int % 1_000_000_000:09d}"
    matching_cpv = f"98{uuid.uuid4().int % 1_000_000:06d}"
    seed_record["cpvItems"] = [matching_cpv]

    try:
        async with engine.connect() as conn:
            tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
            await conn.execute(tenants.insert().values(id=tenant_id, name="Test Tenant"))
            await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))

            matching_rule_id = uuid.uuid4()
            await conn.execute(
                alert_rules.insert().values(
                    id=matching_rule_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name="matching cleaning-services rule",
                    event_types=["contract.created", "contract.modified"],
                    filters={"cpv_prefix": matching_cpv},
                    schedule="IMMEDIATE",
                    delivery_channels=["IN_APP"],
                )
            )
            non_matching_rule_id = uuid.uuid4()
            await conn.execute(
                alert_rules.insert().values(
                    id=non_matching_rule_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name="non-matching CPV rule",
                    event_types=["contract.created", "contract.modified"],
                    filters={"cpv_prefix": "45"},
                    schedule="IMMEDIATE",
                    delivery_channels=["IN_APP"],
                )
            )
            await conn.commit()

            # 1. first ingestion -> contract.created, matching rule fires once
            result = await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=seed_record,
                payload_uri="mem://seed-1",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            fired = await evaluate_and_fire(
                conn, act_upsert=result.act_upsert, delivery_channel=delivery_channel
            )
            assert fired >= 1

            events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == matching_rule_id))
            ).all()
            assert len(events) == 1
            assert events[0].event_type == "contract.created"

            non_matching_events = (
                await conn.execute(
                    select(alert_events).where(alert_events.c.alert_rule_id == non_matching_rule_id)
                )
            ).all()
            assert len(non_matching_events) == 0

            # 2. re-ingesting the identical payload is a no-op upstream (dedup on
            # content hash means ingest_khmdhs_record won't even call upsert_act
            # again), so re-running evaluate_and_fire with the same act_upsert
            # must not create a second contract.created event.
            fired_again = await evaluate_and_fire(
                conn, act_upsert=result.act_upsert, delivery_channel=delivery_channel
            )
            assert fired_again == 0
            events_after_dedup = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == matching_rule_id))
            ).all()
            assert len(events_after_dedup) == 1

            # 3. a material amount change -> contract.modified fires (different
            # material_change_hash than the "created" event, so it's a new row)
            modified_record = dict(seed_record)
            modified_record["totalCostWithVAT"] = 150000.00
            modified_result = await ingest_khmdhs_record(
                conn,
                resource="contract",
                raw_record=modified_record,
                payload_uri="mem://seed-2",
                content_sha256=f"sha-{uuid.uuid4()}",
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
            )
            assert modified_result.act_upsert.is_new is False
            assert "amount_gross" in modified_result.act_upsert.changed_fields

            fired_modified = await evaluate_and_fire(
                conn, act_upsert=modified_result.act_upsert, delivery_channel=delivery_channel
            )
            assert fired_modified >= 1

            all_events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == matching_rule_id))
            ).all()
            assert {e.event_type for e in all_events} == {"contract.created", "contract.modified"}
    finally:
        await engine.dispose()
