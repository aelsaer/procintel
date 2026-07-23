"""Pure date-window computation (`scheduler.compute_window`) — the logic
`run_due_jobs` uses to decide whether a job is due and, if so, which
window to run. Split out precisely so this is testable without a
database; `run_due_jobs` itself (locking, cursor writes, connector_runs
rows) needs a real Postgres instance — see
tests/integration/test_orchestration_scheduler_db.py.
"""

from datetime import date, datetime, timedelta, timezone

from services.ingestion.orchestration.scheduler import ScheduledJob, compute_window

NOW = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)


def _job(**overrides) -> ScheduledJob:
    defaults = dict(
        source_system="KHMDHS",
        resource_type="ALL",
        partition_key="GLOBAL",
        window_days=30,
        backfill_start_date=date(2024, 1, 1),
        min_interval=timedelta(hours=1),
        run_window=None,
    )
    defaults.update(overrides)
    return ScheduledJob(**defaults)


def test_no_cursor_yet_starts_from_backfill_start_date():
    job = _job()
    window = compute_window(cursor_value=None, last_success_at=None, job=job, now=NOW)
    assert window == (date(2024, 1, 1), date(2024, 1, 30))  # 30-day window from backfill_start_date


def test_window_is_capped_at_today():
    job = _job(backfill_start_date=date(2025, 6, 1), window_days=30)
    window = compute_window(cursor_value=None, last_success_at=None, job=job, now=NOW)
    assert window == (date(2025, 6, 1), date(2025, 6, 15))  # capped at NOW's date, not the full 30 days


def test_resumes_from_the_day_after_the_last_ingested_date():
    job = _job(window_days=30)
    window = compute_window(
        cursor_value={"last_ingested_date": "2025-05-01"},
        last_success_at=NOW - timedelta(days=2),
        job=job,
        now=NOW,
    )
    assert window == (date(2025, 5, 2), date(2025, 5, 31))


def test_not_due_within_min_interval_of_last_success():
    job = _job(min_interval=timedelta(hours=1))
    window = compute_window(
        cursor_value={"last_ingested_date": "2025-06-14"},
        last_success_at=NOW - timedelta(minutes=30),
        job=job,
        now=NOW,
    )
    assert window is None


def test_due_once_min_interval_has_elapsed():
    job = _job(min_interval=timedelta(hours=1))
    window = compute_window(
        cursor_value={"last_ingested_date": "2025-06-14"},
        last_success_at=NOW - timedelta(hours=2),
        job=job,
        now=NOW,
    )
    assert window == (date(2025, 6, 15), date(2025, 6, 15))


def test_already_caught_up_to_today_returns_none():
    job = _job(min_interval=timedelta(seconds=0))
    window = compute_window(
        cursor_value={"last_ingested_date": "2025-06-15"},
        last_success_at=NOW - timedelta(days=1),
        job=job,
        now=NOW,
    )
    assert window is None


def test_rolling_job_starts_with_recent_overlap_instead_of_historical_backfill():
    job = _job(min_interval=timedelta(hours=23), rolling_lookback_days=3)

    window = compute_window(cursor_value=None, last_success_at=None, job=job, now=NOW)

    assert window == (date(2025, 6, 13), date(2025, 6, 15))


def test_rolling_job_rechecks_overlap_after_interval_even_with_older_cursor():
    job = _job(min_interval=timedelta(hours=23), rolling_lookback_days=3)

    window = compute_window(
        cursor_value={"last_ingested_date": "2025-05-01"},
        last_success_at=NOW - timedelta(hours=24),
        job=job,
        now=NOW,
    )

    assert window == (date(2025, 6, 13), date(2025, 6, 15))
