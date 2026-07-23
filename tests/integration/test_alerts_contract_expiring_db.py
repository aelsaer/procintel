"""`contract.expiring` (§30.5) against a real Postgres instance.

Skipped automatically unless $DATABASE_URL is set. Unlike every other event
type in `services/alerts/evaluate.py`, this one isn't triggered by an
ingestion upsert — `evaluate_expiring_contracts_and_fire()` is a time-based
scan a periodic caller (a cron-like scheduler, none of which exists in
this codebase yet) would invoke. Seeds three CONTRACT acts: one expiring
in 10 days (within a 30-day window), one expiring in 100 days (outside
it), one with no `end_date` at all (never scanned) — and confirms only
the first fires, then confirms re-running the *same* scan the next day is
a distinct event, not deduplicated away (the point of including `as_of` in
the material-change hash: a contract getting closer to expiry deserves a
fresh reminder each day, not silence after the first alert).
"""

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import alert_events, alert_rules, tenants, users
from services.alerts.delivery import LogDeliveryChannel
from services.alerts.evaluate import evaluate_expiring_contracts_and_fire
from services.ingestion.connectors.khmdhs.db_writer import ingest_khmdhs_record

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

CONTRACT_RECORD = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json").read_text(
        encoding="utf-8"
    )
)["data"][0]


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_only_the_soon_to_expire_contract_fires_and_reruns_are_distinct():
    engine = create_async_engine(_asyncpg_url())
    delivery_channel = LogDeliveryChannel()
    today = date(2025, 6, 1)

    try:
        async with engine.connect() as conn:
            tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
            await conn.execute(tenants.insert().values(id=tenant_id, name="Test Tenant"))
            await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))
            rule_id = uuid.uuid4()
            await conn.execute(
                alert_rules.insert().values(
                    id=rule_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name="expiring contracts",
                    event_types=["contract.expiring"],
                    filters={},
                    schedule="IMMEDIATE",
                    delivery_channels=["IN_APP"],
                )
            )
            await conn.commit()

            soon_record = dict(
                CONTRACT_RECORD, referenceNumber="25SYMV000000001", endDate=(today + timedelta(days=10)).isoformat()
            )
            far_record = dict(
                CONTRACT_RECORD, referenceNumber="25SYMV000000002", endDate=(today + timedelta(days=100)).isoformat()
            )
            # inherits CONTRACT_RECORD's fields as-is, with no endDate at all -> end_date stays NULL
            no_end_date_record = dict(CONTRACT_RECORD, referenceNumber="25SYMV000000003")

            for record in (soon_record, far_record, no_end_date_record):
                await ingest_khmdhs_record(
                    conn,
                    resource="contract",
                    raw_record=record,
                    payload_uri=f"mem://{record['referenceNumber']}",
                    content_sha256=f"sha-{uuid.uuid4()}",
                    http_status=200,
                    fetched_at=datetime.now(timezone.utc),
                )
            await conn.commit()

            fired = await evaluate_expiring_contracts_and_fire(conn, delivery_channel=delivery_channel, as_of=today)
            assert fired == 1

            events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(events) == 1
            assert events[0].event_type == "contract.expiring"
            assert events[0].payload["end_date"] == (today + timedelta(days=10)).isoformat()

            # re-running the same day's scan is deduped (identical material_change_hash)
            fired_again_same_day = await evaluate_expiring_contracts_and_fire(
                conn, delivery_channel=delivery_channel, as_of=today
            )
            assert fired_again_same_day == 0

            # running it again the next day is a *new*, distinct event (not deduped)
            fired_next_day = await evaluate_expiring_contracts_and_fire(
                conn, delivery_channel=delivery_channel, as_of=today + timedelta(days=1)
            )
            assert fired_next_day == 1

            all_events = (
                await conn.execute(select(alert_events).where(alert_events.c.alert_rule_id == rule_id))
            ).all()
            assert len(all_events) == 2
    finally:
        await engine.dispose()
