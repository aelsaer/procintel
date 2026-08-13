import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import opportunity_score_jobs, tenants
from services.analytics.scoring_worker import recover_stale_scoring_job


DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _async_url() -> str:
    assert DATABASE_URL is not None
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_stale_scoring_lease_is_requeued():
    tenant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    engine = create_async_engine(_async_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                tenants.insert().values(id=tenant_id, name="Scoring recovery", plan="STARTER")
            )
            await conn.execute(
                opportunity_score_jobs.insert().values(
                    tenant_id=tenant_id,
                    status="RUNNING",
                    reason="TEST",
                    requested_at=now - timedelta(hours=1),
                    started_at=now - timedelta(hours=1),
                )
            )

        async with engine.connect() as conn:
            assert await recover_stale_scoring_job(conn, tenant_id, now=now) == 1
            row = (
                await conn.execute(
                    sa.select(opportunity_score_jobs).where(
                        opportunity_score_jobs.c.tenant_id == tenant_id
                    )
                )
            ).one()
            assert row.status == "QUEUED"
            assert row.started_at is None
            assert row.error == {"message": "Recovered stale worker lease"}
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                opportunity_score_jobs.delete().where(
                    opportunity_score_jobs.c.tenant_id == tenant_id
                )
            )
            await conn.execute(tenants.delete().where(tenants.c.id == tenant_id))
        await engine.dispose()
