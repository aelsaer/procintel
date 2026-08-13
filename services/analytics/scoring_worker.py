"""Queue worker for tenant-relative opportunity score recomputation."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from packages.domain.tables import opportunity_score_jobs
from packages.tenancy import all_tenant_ids, tenant_session

from .opportunity_scoring import OpportunityScoreRun, score_opportunities_for_tenant


async def process_next_scoring_job(conn: AsyncConnection) -> OpportunityScoreRun | None:
    row = (await conn.execute(
        sa.select(opportunity_score_jobs)
        .where(opportunity_score_jobs.c.status.in_(("QUEUED", "FAILED")))
        .order_by(opportunity_score_jobs.c.requested_at)
        .with_for_update(skip_locked=True).limit(1)
    )).first()
    if row is None:
        return None
    await conn.execute(opportunity_score_jobs.update().where(
        opportunity_score_jobs.c.tenant_id == row.tenant_id,
    ).values(status="RUNNING", started_at=datetime.now(timezone.utc), error=None))
    await conn.commit()
    try:
        result = await score_opportunities_for_tenant(conn, tenant_id=row.tenant_id)
    except Exception as exc:
        await conn.rollback()
        await conn.execute(opportunity_score_jobs.update().where(
            opportunity_score_jobs.c.tenant_id == row.tenant_id,
            opportunity_score_jobs.c.requested_at == row.requested_at,
            opportunity_score_jobs.c.status == "RUNNING",
        ).values(status="FAILED", finished_at=datetime.now(timezone.utc), error={"message": str(exc)}))
        await conn.commit()
        raise
    await conn.execute(opportunity_score_jobs.update().where(
        opportunity_score_jobs.c.tenant_id == row.tenant_id,
        opportunity_score_jobs.c.requested_at == row.requested_at,
        opportunity_score_jobs.c.status == "RUNNING",
    ).values(status="SUCCEEDED", finished_at=datetime.now(timezone.utc), error=None))
    await conn.commit()
    return result


async def process_scoring_job_for_tenant(
    conn: AsyncConnection, tenant_id: uuid.UUID,
) -> OpportunityScoreRun | None:
    row = (await conn.execute(
        sa.select(opportunity_score_jobs)
        .where(
            opportunity_score_jobs.c.tenant_id == tenant_id,
            opportunity_score_jobs.c.status.in_(("QUEUED", "FAILED")),
        ).with_for_update(skip_locked=True)
    )).first()
    if row is None:
        return None
    await conn.execute(
        opportunity_score_jobs.update()
        .where(opportunity_score_jobs.c.tenant_id == tenant_id)
        .values(status="RUNNING", started_at=datetime.now(timezone.utc), error=None)
    )
    await conn.commit()
    try:
        result = await score_opportunities_for_tenant(conn, tenant_id=tenant_id)
    except Exception as exc:
        await conn.rollback()
        await conn.execute(
            opportunity_score_jobs.update()
            .where(
                opportunity_score_jobs.c.tenant_id == tenant_id,
                opportunity_score_jobs.c.requested_at == row.requested_at,
                opportunity_score_jobs.c.status == "RUNNING",
            )
            .values(status="FAILED", finished_at=datetime.now(timezone.utc), error={"message": str(exc)})
        )
        await conn.commit()
        raise
    await conn.execute(
        opportunity_score_jobs.update()
        .where(
            opportunity_score_jobs.c.tenant_id == tenant_id,
            opportunity_score_jobs.c.requested_at == row.requested_at,
            opportunity_score_jobs.c.status == "RUNNING",
        )
        .values(status="SUCCEEDED", finished_at=datetime.now(timezone.utc), error=None)
    )
    await conn.commit()
    return result


async def process_scoring_job_by_tenant(tenant_id: uuid.UUID) -> None:
    database_url = os.environ["DATABASE_URL"]
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            async with tenant_session(conn, tenant_id):
                await process_scoring_job_for_tenant(conn, tenant_id)
    finally:
        await engine.dispose()


async def recover_stale_scoring_job(
    conn: AsyncConnection,
    tenant_id: uuid.UUID,
    *,
    stale_after: timedelta = timedelta(minutes=30),
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - stale_after
    result = await conn.execute(
        opportunity_score_jobs.update()
        .where(
            opportunity_score_jobs.c.tenant_id == tenant_id,
            opportunity_score_jobs.c.status == "RUNNING",
            sa.or_(
                opportunity_score_jobs.c.started_at.is_(None),
                opportunity_score_jobs.c.started_at < cutoff,
            ),
        )
        .values(
            status="QUEUED",
            started_at=None,
            error={"message": "Recovered stale worker lease"},
        )
    )
    await conn.commit()
    return result.rowcount


async def process_pending_scoring_jobs(conn: AsyncConnection) -> int:
    """Process at most one durable scoring job for each tenant."""
    processed = 0
    for tenant_id in await all_tenant_ids(conn):
        try:
            async with tenant_session(conn, tenant_id):
                await recover_stale_scoring_job(conn, tenant_id)
                if await process_scoring_job_for_tenant(conn, tenant_id) is not None:
                    processed += 1
        except Exception:  # the job row already contains the tenant-specific error
            continue
    return processed
