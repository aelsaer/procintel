"""Cursor/watermark-driven job scheduling — description.txt §35-36, Στάδιο 1.

No Celery/Redis/Prefect/Dagster here, even though §11's recommended stack
names them: every other MVP-scope decision this codebase has made so far
("Postgres is enough" for `/v1/search`, the analytics marts, alerts) picked
Postgres over new infra when Postgres could do the job, and a due-job scan
over `source_cursors`/`mart_refresh_state` plus `pg_advisory_lock` for
mutual exclusion covers exactly what's needed here: run each job at most
once per its interval, don't double-run if two schedulers overlap, resume
from the last successful watermark after a crash. This is a real,
production-operable pattern (driven by a cron entry, a systemd timer, or a
Kubernetes CronJob calling `cli.py run-once`), not a toy — adding a real
queue/broker is a legitimate future upgrade if concurrency needs outgrow a
single scheduler process, not a gap in this implementation.

A cursor only advances (`last_success_at`, `cursor_value`) after
`run_window` returns without raising — matching source_cursors' own
migration comment: "Only advances after all pages are fetched, raw payload
stored, staging completed and failures recorded". A raised exception
updates `last_error`/`last_attempt_at` but leaves the watermark where it
was, so the same window is retried next pass.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import connector_runs, source_cursors
from packages.source_clients.pg_lock import advisory_unlock, try_advisory_lock

RunWindow = Callable[[AsyncConnection, date, date], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ScheduledJob:
    source_system: str
    resource_type: str
    partition_key: str
    window_days: int
    backfill_start_date: date
    min_interval: timedelta  # how long a successful run is considered "fresh" before due again
    run_window: RunWindow
    # Daily ingestion re-reads a short trailing window so late publications
    # and corrected source records are picked up without replaying history.
    # Historical backfills keep this at zero and continue to use the cursor.
    rolling_lookback_days: int = 0


@dataclass(frozen=True)
class JobRunOutcome:
    job: ScheduledJob
    ran: bool
    skipped_reason: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


def _lock_key(job: ScheduledJob) -> str:
    return f"procintel:orchestration:{job.source_system}:{job.resource_type}:{job.partition_key}"


def compute_window(
    *,
    cursor_value: dict[str, Any] | None,
    last_success_at: datetime | None,
    job: ScheduledJob,
    now: datetime,
) -> tuple[date, date] | None:
    """Pure date-window logic, split out from `run_due_jobs` so it's unit
    testable without a database. Returns None if the job isn't due yet
    (still within `min_interval` of its last success) or is already caught
    up to today (date_from would be in the future)."""
    if job.window_days <= 0:
        raise ValueError("window_days must be positive")
    if job.rolling_lookback_days < 0:
        raise ValueError("rolling_lookback_days must be non-negative")

    if last_success_at is not None and now - last_success_at < job.min_interval:
        return None

    today = now.date()
    if job.rolling_lookback_days:
        rolling_date_from = max(
            job.backfill_start_date,
            today - timedelta(days=job.rolling_lookback_days - 1),
        )
        if cursor_value and cursor_value.get("last_ingested_date"):
            next_unseen_date = date.fromisoformat(
                str(cursor_value["last_ingested_date"])
            ) + timedelta(days=1)
            if next_unseen_date < rolling_date_from:
                date_from = max(job.backfill_start_date, next_unseen_date)
                date_to = min(
                    date_from + timedelta(days=job.window_days - 1),
                    today,
                )
                return date_from, date_to
        return rolling_date_from, today

    if last_success_at is not None:
        last_ingested = date.fromisoformat(cursor_value["last_ingested_date"]) if cursor_value else job.backfill_start_date
        date_from = last_ingested + timedelta(days=1)
    else:
        date_from = job.backfill_start_date

    if date_from > today:
        return None
    date_to = min(date_from + timedelta(days=job.window_days - 1), today)
    return date_from, date_to


async def run_due_jobs(
    conn: AsyncConnection, jobs: list[ScheduledJob], *, now: datetime | None = None
) -> list[JobRunOutcome]:
    now = now or datetime.now(timezone.utc)
    outcomes: list[JobRunOutcome] = []

    for job in jobs:
        lock_key = _lock_key(job)
        if not await try_advisory_lock(conn, lock_key):
            outcomes.append(JobRunOutcome(job=job, ran=False, skipped_reason="locked by another scheduler"))
            continue

        try:
            cursor_row = (
                await conn.execute(
                    select(source_cursors).where(
                        source_cursors.c.source_system == job.source_system,
                        source_cursors.c.resource_type == job.resource_type,
                        source_cursors.c.partition_key == job.partition_key,
                    )
                )
            ).first()

            window = compute_window(
                cursor_value=cursor_row.cursor_value if cursor_row is not None else None,
                last_success_at=cursor_row.last_success_at if cursor_row is not None else None,
                job=job,
                now=now,
            )
            if window is None:
                outcomes.append(JobRunOutcome(job=job, ran=False, skipped_reason="not due"))
                continue
            date_from, date_to = window

            run_id = uuid.uuid4()
            partition_label = f"{date_from.isoformat()}:{date_to.isoformat()}"
            await conn.execute(
                connector_runs.insert().values(
                    id=run_id,
                    source_system=job.source_system,
                    resource_type=job.resource_type,
                    partition_key=partition_label,
                    run_type="INCREMENTAL",
                    status="RUNNING",
                    triggered_by="SCHEDULE",
                )
            )
            if cursor_row is None:
                await conn.execute(
                    source_cursors.insert().values(
                        source_system=job.source_system,
                        resource_type=job.resource_type,
                        partition_key=job.partition_key,
                        cursor_value={},
                        last_attempt_at=now,
                    )
                )
            else:
                await conn.execute(
                    source_cursors.update()
                    .where(
                        source_cursors.c.source_system == job.source_system,
                        source_cursors.c.resource_type == job.resource_type,
                        source_cursors.c.partition_key == job.partition_key,
                    )
                    .values(last_attempt_at=now)
                )
            await conn.commit()

            try:
                result = await job.run_window(conn, date_from, date_to)
            except Exception as exc:  # noqa: BLE001 — deliberately broad: any failure must not advance the watermark
                await conn.execute(
                    source_cursors.update()
                    .where(
                        source_cursors.c.source_system == job.source_system,
                        source_cursors.c.resource_type == job.resource_type,
                        source_cursors.c.partition_key == job.partition_key,
                    )
                    .values(last_error={"message": str(exc)})
                )
                await conn.execute(
                    connector_runs.update()
                    .where(connector_runs.c.id == run_id)
                    .values(status="FAILED", finished_at=datetime.now(timezone.utc), error={"message": str(exc)})
                )
                await conn.commit()
                outcomes.append(
                    JobRunOutcome(job=job, ran=False, date_from=date_from, date_to=date_to, error=str(exc))
                )
                continue

            core_failures = int(result.get("records_failed", 0))
            enrichment_failures = int(result.get("enrichment_failed", 0)) + int(
                result.get("enrichment_callbacks_failed", 0)
            )
            partial_failures = core_failures + enrichment_failures
            run_status = "PARTIAL" if partial_failures else "SUCCEEDED"
            run_error = None
            if partial_failures:
                run_error = {
                    "message": f"{partial_failures} record/enrichment operation(s) failed",
                    "core_failures": core_failures,
                    "enrichment_failures": enrichment_failures,
                    "failures": (
                        result.get("record_failures", [])
                        + result.get("enrichment_failures", [])
                    )[:10],
                }

            cursor_values: dict[str, Any] = {
                "last_error": run_error,
            }
            if core_failures == 0:
                cursor_values.update(
                    cursor_value={"last_ingested_date": date_to.isoformat()},
                    last_success_at=now,
                )
            await conn.execute(
                source_cursors.update()
                .where(
                    source_cursors.c.source_system == job.source_system,
                    source_cursors.c.resource_type == job.resource_type,
                    source_cursors.c.partition_key == job.partition_key,
                )
                .values(**cursor_values)
            )
            await conn.execute(
                connector_runs.update()
                .where(connector_runs.c.id == run_id)
                .values(
                    status=run_status,
                    finished_at=datetime.now(timezone.utc),
                    records_fetched=result.get("records_fetched", 0),
                    records_upserted=result.get("records_upserted", 0),
                    records_unchanged=result.get("records_unchanged", 0),
                    records_failed=core_failures,
                    enrichment_succeeded=sum(
                        int(value)
                        for value in result.get("enrichment_succeeded", {}).values()
                    ),
                    enrichment_failed=enrichment_failures,
                    enrichment_deferred=sum(
                        int(value)
                        for value in result.get("enrichment_deferred", {}).values()
                    ),
                    pages_fetched=result.get("pages_fetched", 0),
                    metrics={
                        key: value
                        for key, value in result.items()
                        if key
                        not in {
                            "record_failures",
                            "enrichment_failures",
                        }
                    },
                    error=run_error,
                )
            )
            await conn.commit()
            outcomes.append(JobRunOutcome(job=job, ran=True, date_from=date_from, date_to=date_to, result=result))
        finally:
            await advisory_unlock(conn, lock_key)

    return outcomes
