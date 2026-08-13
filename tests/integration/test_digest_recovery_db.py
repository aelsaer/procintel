from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import alert_digest_runs, alert_rules, tenants, users
from services.alerts.digests import _claim_digest_run

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_stale_digest_lease_is_recovered_atomically():
    engine = create_async_engine(_async_url())
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    try:
        async with engine.connect() as conn:
            await conn.execute(tenants.insert().values(id=tenant_id, name="Digest recovery"))
            await conn.execute(
                users.insert().values(
                    id=user_id,
                    email=f"digest-{user_id}@example.test",
                )
            )
            await conn.execute(
                alert_rules.insert().values(
                    id=rule_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name="Recover daily digest",
                    event_types=["opportunity.created"],
                    filters={},
                    schedule="DAILY_DIGEST",
                    delivery_channels=["IN_APP"],
                )
            )
            await conn.execute(
                alert_digest_runs.insert().values(
                    id=run_id,
                    tenant_id=tenant_id,
                    alert_rule_id=rule_id,
                    schedule="DAILY_DIGEST",
                    period_key="2026-08-13",
                    period_started_at=now - timedelta(days=1),
                    period_ended_at=now,
                    status="RUNNING",
                    channels=["IN_APP"],
                    attempt_count=1,
                    last_attempt_at=now - timedelta(hours=1),
                )
            )
            await conn.commit()

            claim = await _claim_digest_run(
                conn,
                run_id=uuid.uuid4(),
                tenant_id=tenant_id,
                alert_rule_id=rule_id,
                schedule="DAILY_DIGEST",
                period_key="2026-08-13",
                period_start=now - timedelta(days=1),
                period_end=now,
                event_count=0,
                channels=["IN_APP"],
                now=now,
            )

            assert claim == (run_id, 2)
            row = (
                await conn.execute(
                    alert_digest_runs.select().where(alert_digest_runs.c.id == run_id)
                )
            ).first()
            assert row is not None
            assert row.status == "RUNNING"
            assert row.last_attempt_at == now

            await conn.execute(alert_digest_runs.delete().where(alert_digest_runs.c.id == run_id))
            await conn.execute(alert_rules.delete().where(alert_rules.c.id == rule_id))
            await conn.execute(users.delete().where(users.c.id == user_id))
            await conn.execute(tenants.delete().where(tenants.c.id == tenant_id))
            await conn.commit()
    finally:
        await engine.dispose()
