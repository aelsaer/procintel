"""Postgres-backed, idempotent queue for rate-limited provider enrichment."""

from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import enrichment_jobs


@dataclass(frozen=True)
class EnrichmentJobRef:
    id: uuid.UUID
    provider: str
    idempotency_key: str
    status: str
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True)
class ClaimedEnrichmentJob:
    id: uuid.UUID
    provider: str
    idempotency_key: str
    payload: dict[str, Any]
    object_type: str | None
    object_id: uuid.UUID | None
    source_record_id: uuid.UUID | None
    attempt_count: int
    max_attempts: int


def _retry_delay(attempt_count: int) -> timedelta:
    return timedelta(minutes=min(24 * 60, 2 ** min(max(attempt_count, 1), 10)))


async def enqueue_enrichment(
    conn: AsyncConnection,
    *,
    provider: str,
    idempotency_key: str,
    payload: dict[str, Any],
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    source_record_id: uuid.UUID | None = None,
    priority: int = 100,
    max_attempts: int = 8,
) -> EnrichmentJobRef:
    now = datetime.now(timezone.utc)
    permanent_dead = sa.and_(
        enrichment_jobs.c.status == "DEAD",
        sa.func.coalesce(
            enrichment_jobs.c.last_error["permanent"].as_boolean(),
            False,
        ).is_(True),
    )
    reopenable_dead = sa.and_(
        enrichment_jobs.c.status == "DEAD",
        sa.not_(permanent_dead),
    )
    values = {
        "id": uuid.uuid4(),
        "provider": provider,
        "idempotency_key": idempotency_key,
        "object_type": object_type,
        "object_id": object_id,
        "source_record_id": source_record_id,
        "payload": payload,
        "priority": priority,
        "max_attempts": max_attempts,
        "available_at": now,
        "updated_at": now,
    }
    statement = (
        pg_insert(enrichment_jobs)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[
                enrichment_jobs.c.provider,
                enrichment_jobs.c.idempotency_key,
            ],
            set_={
                "payload": payload,
                "object_type": object_type,
                "object_id": object_id,
                "source_record_id": source_record_id,
                "priority": priority,
                "max_attempts": max_attempts,
                "updated_at": now,
                "attempt_count": sa.case(
                    (
                        reopenable_dead,
                        0,
                    ),
                    else_=enrichment_jobs.c.attempt_count,
                ),
                "status": sa.case(
                    (
                        reopenable_dead,
                        "QUEUED",
                    ),
                    else_=enrichment_jobs.c.status,
                ),
                "available_at": sa.case(
                    (
                        reopenable_dead,
                        now,
                    ),
                    else_=enrichment_jobs.c.available_at,
                ),
                "finished_at": sa.case(
                    (
                        reopenable_dead,
                        None,
                    ),
                    else_=enrichment_jobs.c.finished_at,
                ),
            },
        )
        .returning(
            enrichment_jobs.c.id,
            enrichment_jobs.c.provider,
            enrichment_jobs.c.idempotency_key,
            enrichment_jobs.c.status,
            enrichment_jobs.c.attempt_count,
            enrichment_jobs.c.max_attempts,
        )
    )
    row = (await conn.execute(statement)).one()
    return EnrichmentJobRef(**row._mapping)


async def start_enrichment(
    conn: AsyncConnection,
    job_id: uuid.UUID,
    *,
    worker_id: str | None = None,
) -> bool:
    now = datetime.now(timezone.utc)
    row = (
        await conn.execute(
            enrichment_jobs.update()
            .where(
                enrichment_jobs.c.id == job_id,
                enrichment_jobs.c.status.in_(("QUEUED", "FAILED")),
                enrichment_jobs.c.available_at <= now,
                enrichment_jobs.c.attempt_count < enrichment_jobs.c.max_attempts,
            )
            .values(
                status="RUNNING",
                attempt_count=enrichment_jobs.c.attempt_count + 1,
                locked_at=now,
                locked_by=worker_id or socket.gethostname(),
                last_error=None,
                updated_at=now,
            )
            .returning(enrichment_jobs.c.id)
        )
    ).first()
    return row is not None


async def complete_enrichment(
    conn: AsyncConnection,
    job_id: uuid.UUID,
    *,
    result: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    await conn.execute(
        enrichment_jobs.update()
        .where(enrichment_jobs.c.id == job_id)
        .values(
            status="SUCCEEDED",
            result=result or {},
            last_error=None,
            locked_at=None,
            locked_by=None,
            updated_at=now,
            finished_at=now,
        )
    )


async def fail_enrichment(
    conn: AsyncConnection,
    job_id: uuid.UUID,
    *,
    error: dict[str, Any],
    blocked_config: bool = False,
    blocked_upstream: bool = False,
    permanent: bool = False,
) -> None:
    if blocked_config and blocked_upstream:
        raise ValueError("an enrichment cannot be blocked by config and upstream")
    row = (
        await conn.execute(
            sa.select(
                enrichment_jobs.c.attempt_count,
                enrichment_jobs.c.max_attempts,
            ).where(enrichment_jobs.c.id == job_id)
        )
    ).one()
    now = datetime.now(timezone.utc)
    terminal = permanent or row.attempt_count >= row.max_attempts
    status = (
        "BLOCKED_CONFIG"
        if blocked_config
        else "BLOCKED_UPSTREAM"
        if blocked_upstream
        else "DEAD"
        if terminal
        else "FAILED"
    )
    stored_error = {**error, "permanent": True} if permanent else error
    await conn.execute(
        enrichment_jobs.update()
        .where(enrichment_jobs.c.id == job_id)
        .values(
            status=status,
            last_error=stored_error,
            available_at=(
                now
                if blocked_config or blocked_upstream
                else now + _retry_delay(row.attempt_count)
            ),
            locked_at=None,
            locked_by=None,
            updated_at=now,
            finished_at=now if terminal else None,
        )
    )


async def defer_enrichment(
    conn: AsyncConnection,
    job_id: uuid.UUID,
    *,
    refund_attempt: bool = False,
    retry_after: float | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {
        "status": "QUEUED",
        "locked_at": None,
        "locked_by": None,
        "updated_at": now,
    }
    if retry_after is not None:
        values["available_at"] = now + timedelta(
            seconds=max(0.0, retry_after)
        )
    if refund_attempt:
        values["attempt_count"] = sa.func.greatest(
            enrichment_jobs.c.attempt_count - 1,
            0,
        )
    await conn.execute(
        enrichment_jobs.update()
        .where(enrichment_jobs.c.id == job_id)
        .values(**values)
    )


async def claim_enrichment_jobs(
    conn: AsyncConnection,
    *,
    limit: int,
    providers: set[str] | None = None,
    reactivate_blocked_providers: set[str] | None = None,
    worker_id: str | None = None,
) -> list[ClaimedEnrichmentJob]:
    if limit <= 0:
        return []
    now = datetime.now(timezone.utc)
    runnable_status = enrichment_jobs.c.status.in_(("QUEUED", "FAILED"))
    if reactivate_blocked_providers:
        runnable_status = sa.or_(
            runnable_status,
            sa.and_(
                enrichment_jobs.c.status.in_(
                    ("BLOCKED_CONFIG", "BLOCKED_UPSTREAM")
                ),
                enrichment_jobs.c.provider.in_(reactivate_blocked_providers),
            ),
        )
    conditions = [
        runnable_status,
        enrichment_jobs.c.available_at <= now,
        enrichment_jobs.c.attempt_count < enrichment_jobs.c.max_attempts,
    ]
    if providers is not None:
        conditions.append(enrichment_jobs.c.provider.in_(providers))
    rows = (
        await conn.execute(
            sa.select(enrichment_jobs)
            .where(*conditions)
            .order_by(
                enrichment_jobs.c.priority,
                sa.case(
                    (enrichment_jobs.c.status == "FAILED", 0),
                    else_=1,
                ),
                enrichment_jobs.c.available_at,
                enrichment_jobs.c.created_at,
            )
            .limit(limit)
            .with_for_update(of=enrichment_jobs, skip_locked=True)
        )
    ).mappings().all()
    claimed: list[ClaimedEnrichmentJob] = []
    lock_owner = worker_id or socket.gethostname()
    for row in rows:
        await conn.execute(
            enrichment_jobs.update()
            .where(enrichment_jobs.c.id == row["id"])
            .values(
                status="RUNNING",
                attempt_count=row["attempt_count"] + 1,
                locked_at=now,
                locked_by=lock_owner,
                last_error=None,
                updated_at=now,
            )
        )
        claimed.append(
            ClaimedEnrichmentJob(
                id=row["id"],
                provider=row["provider"],
                idempotency_key=row["idempotency_key"],
                payload=row["payload"] or {},
                object_type=row["object_type"],
                object_id=row["object_id"],
                source_record_id=row["source_record_id"],
                attempt_count=row["attempt_count"] + 1,
                max_attempts=row["max_attempts"],
            )
        )
    await conn.commit()
    return claimed


async def recover_stale_enrichment_jobs(
    conn: AsyncConnection,
    *,
    stale_after: timedelta = timedelta(minutes=30),
) -> int:
    cutoff = datetime.now(timezone.utc) - stale_after
    result = await conn.execute(
        enrichment_jobs.update()
        .where(
            enrichment_jobs.c.status == "RUNNING",
            sa.or_(
                enrichment_jobs.c.locked_at.is_(None),
                enrichment_jobs.c.locked_at < cutoff,
            ),
        )
        .values(
            status="QUEUED",
            locked_at=None,
            locked_by=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await conn.commit()
    return int(result.rowcount or 0)
