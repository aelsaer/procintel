"""`run_due_jobs` against a real Postgres instance — the advisory-locking
and watermark-advancing mechanics `compute_window`'s unit tests can't
exercise on their own.

Skipped automatically unless $DATABASE_URL is set.
"""

import os
from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import connector_runs, source_cursors
from services.ingestion.orchestration.scheduler import ScheduledJob, run_due_jobs

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def _cleanup(conn, source_system: str) -> None:
    await conn.execute(source_cursors.delete().where(source_cursors.c.source_system == source_system))
    await conn.execute(connector_runs.delete().where(connector_runs.c.source_system == source_system))
    await conn.commit()


async def test_run_advances_cursor_and_records_a_connector_run():
    calls: list[tuple] = []

    async def run_window(conn, date_from, date_to):
        calls.append((date_from, date_to))
        return {"pages_fetched": 2, "records_fetched": 5, "records_upserted": 3}

    job = ScheduledJob(
        source_system="TEST_SOURCE_A",
        resource_type="ALL",
        partition_key="GLOBAL",
        window_days=30,
        backfill_start_date=date(2024, 1, 1),
        min_interval=timedelta(hours=1),
        run_window=run_window,
    )

    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            await _cleanup(conn, job.source_system)
            try:
                outcomes = await run_due_jobs(conn, [job], now=datetime(2025, 6, 15, tzinfo=timezone.utc))
                assert len(outcomes) == 1
                assert outcomes[0].ran is True
                assert outcomes[0].date_from == date(2024, 1, 1)
                assert outcomes[0].date_to == date(2024, 1, 30)
                assert calls == [(date(2024, 1, 1), date(2024, 1, 30))]

                cursor_row = (
                    await conn.execute(
                        select(source_cursors).where(source_cursors.c.source_system == job.source_system)
                    )
                ).one()
                assert cursor_row.cursor_value == {"last_ingested_date": "2024-01-30"}
                assert cursor_row.last_success_at is not None
                assert cursor_row.last_error is None

                run_row = (
                    await conn.execute(
                        select(connector_runs).where(connector_runs.c.source_system == job.source_system)
                    )
                ).one()
                assert run_row.status == "SUCCEEDED"
                assert run_row.records_upserted == 3
                assert run_row.triggered_by == "SCHEDULE"

                # Immediately re-running is a no-op: still within min_interval of the just-recorded success.
                outcomes_again = await run_due_jobs(conn, [job], now=datetime(2025, 6, 15, 0, 30, tzinfo=timezone.utc))
                assert outcomes_again[0].ran is False
                assert outcomes_again[0].skipped_reason == "not due"
            finally:
                await _cleanup(conn, job.source_system)
    finally:
        await engine.dispose()


async def test_a_failing_run_window_records_the_error_without_advancing_the_cursor():
    async def failing_run_window(conn, date_from, date_to):
        raise RuntimeError("upstream API exploded")

    job = ScheduledJob(
        source_system="TEST_SOURCE_B",
        resource_type="ALL",
        partition_key="GLOBAL",
        window_days=30,
        backfill_start_date=date(2024, 1, 1),
        min_interval=timedelta(hours=1),
        run_window=failing_run_window,
    )

    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            await _cleanup(conn, job.source_system)
            try:
                outcomes = await run_due_jobs(conn, [job], now=datetime(2025, 6, 15, tzinfo=timezone.utc))
                assert outcomes[0].ran is False
                assert "upstream API exploded" in outcomes[0].error

                cursor_row = (
                    await conn.execute(
                        select(source_cursors).where(source_cursors.c.source_system == job.source_system)
                    )
                ).one()
                assert cursor_row.last_success_at is None  # never advanced
                assert cursor_row.last_error == {"message": "upstream API exploded"}

                run_row = (
                    await conn.execute(
                        select(connector_runs).where(connector_runs.c.source_system == job.source_system)
                    )
                ).one()
                assert run_row.status == "FAILED"
            finally:
                await _cleanup(conn, job.source_system)
    finally:
        await engine.dispose()


async def test_a_database_failure_is_rolled_back_before_recording_run_state():
    async def failing_run_window(conn, date_from, date_to):
        await conn.execute(sa.text("SELECT * FROM table_that_does_not_exist_for_scheduler_test"))
        return {}

    job = ScheduledJob(
        source_system="TEST_SOURCE_ABORTED_TRANSACTION",
        resource_type="ALL",
        partition_key="GLOBAL",
        window_days=1,
        backfill_start_date=date(2025, 1, 1),
        min_interval=timedelta(hours=1),
        run_window=failing_run_window,
    )

    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            await _cleanup(conn, job.source_system)
            try:
                outcomes = await run_due_jobs(conn, [job], now=datetime(2025, 6, 15, tzinfo=timezone.utc))
                assert outcomes[0].ran is False
                cursor_row = (
                    await conn.execute(
                        select(source_cursors).where(source_cursors.c.source_system == job.source_system)
                    )
                ).one()
                assert cursor_row.last_success_at is None
                assert "table_that_does_not_exist" in cursor_row.last_error["message"]
                run_row = (
                    await conn.execute(
                        select(connector_runs).where(connector_runs.c.source_system == job.source_system)
                    )
                ).one()
                assert run_row.status == "FAILED"
            finally:
                await _cleanup(conn, job.source_system)
    finally:
        await engine.dispose()


async def test_partial_core_failure_does_not_advance_cursor_but_preserves_failure_details():
    async def partial_run_window(conn, date_from, date_to):
        return {
            "pages_fetched": 1,
            "records_fetched": 3,
            "records_upserted": 2,
            "records_failed": 1,
            "record_failures": [
                {"adam": "BAD-ADAM", "stage": "ingest", "error": "IntegrityError"}
            ],
        }

    job = ScheduledJob(
        source_system="TEST_SOURCE_PARTIAL_CORE",
        resource_type="ALL",
        partition_key="GLOBAL",
        window_days=2,
        backfill_start_date=date(2026, 7, 28),
        min_interval=timedelta(hours=1),
        run_window=partial_run_window,
    )
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            await _cleanup(conn, job.source_system)
            try:
                outcomes = await run_due_jobs(
                    conn,
                    [job],
                    now=datetime(2026, 7, 29, tzinfo=timezone.utc),
                )
                assert outcomes[0].ran is True
                cursor_row = (
                    await conn.execute(
                        select(source_cursors).where(
                            source_cursors.c.source_system == job.source_system
                        )
                    )
                ).one()
                assert cursor_row.cursor_value == {}
                assert cursor_row.last_success_at is None
                assert cursor_row.last_error["core_failures"] == 1

                run_row = (
                    await conn.execute(
                        select(connector_runs).where(
                            connector_runs.c.source_system == job.source_system
                        )
                    )
                ).one()
                assert run_row.status == "PARTIAL"
                assert run_row.error["failures"][0]["adam"] == "BAD-ADAM"
            finally:
                await _cleanup(conn, job.source_system)
    finally:
        await engine.dispose()


async def test_enrichment_only_partial_advances_primary_source_cursor():
    async def enrichment_partial_run_window(conn, date_from, date_to):
        return {
            "pages_fetched": 1,
            "records_fetched": 2,
            "records_upserted": 2,
            "records_failed": 0,
            "enrichment_failed": 1,
            "enrichment_failures": [
                {"provider": "MEF", "adam": "A1", "error": "ReadTimeout"}
            ],
        }

    job = ScheduledJob(
        source_system="TEST_SOURCE_PARTIAL_ENRICHMENT",
        resource_type="ALL",
        partition_key="GLOBAL",
        window_days=2,
        backfill_start_date=date(2026, 7, 28),
        min_interval=timedelta(hours=1),
        run_window=enrichment_partial_run_window,
    )
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            await _cleanup(conn, job.source_system)
            try:
                await run_due_jobs(
                    conn,
                    [job],
                    now=datetime(2026, 7, 29, tzinfo=timezone.utc),
                )
                cursor_row = (
                    await conn.execute(
                        select(source_cursors).where(
                            source_cursors.c.source_system == job.source_system
                        )
                    )
                ).one()
                assert cursor_row.cursor_value == {
                    "last_ingested_date": "2026-07-29"
                }
                assert cursor_row.last_success_at is not None
                assert cursor_row.last_error["enrichment_failures"] == 1
            finally:
                await _cleanup(conn, job.source_system)
    finally:
        await engine.dispose()


async def test_concurrent_schedulers_do_not_double_run_the_same_partition():
    """Two connections racing for the same (source, resource, partition):
    the second must see the advisory lock held and skip, not run
    concurrently with the first."""
    run_started = False
    run_finished = False

    async def slow_run_window(conn, date_from, date_to):
        nonlocal run_started, run_finished
        run_started = True
        import asyncio

        await asyncio.sleep(0.3)
        run_finished = True
        return {"pages_fetched": 1, "records_fetched": 1, "records_upserted": 1}

    job = ScheduledJob(
        source_system="TEST_SOURCE_C",
        resource_type="ALL",
        partition_key="GLOBAL",
        window_days=30,
        backfill_start_date=date(2024, 1, 1),
        min_interval=timedelta(hours=1),
        run_window=slow_run_window,
    )

    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as setup_conn:
            await _cleanup(setup_conn, job.source_system)

        import asyncio

        async def _first_runner():
            async with engine.connect() as conn:
                return await run_due_jobs(conn, [job], now=datetime(2025, 6, 15, tzinfo=timezone.utc))

        async def _second_runner():
            await asyncio.sleep(0.05)  # let the first one grab the lock first
            async with engine.connect() as conn:
                return await run_due_jobs(conn, [job], now=datetime(2025, 6, 15, tzinfo=timezone.utc))

        first_outcomes, second_outcomes = await asyncio.gather(_first_runner(), _second_runner())

        assert first_outcomes[0].ran is True
        assert second_outcomes[0].ran is False
        assert second_outcomes[0].skipped_reason == "locked by another scheduler"

        async with engine.connect() as conn:
            await _cleanup(conn, job.source_system)
    finally:
        await engine.dispose()
