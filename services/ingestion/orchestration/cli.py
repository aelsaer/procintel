"""Manual/scheduled CLI entrypoint.

    python -m services.ingestion.orchestration.cli run-once
    python -m services.ingestion.orchestration.cli run-daily --at 02:30 --timezone Europe/Athens
    python -m services.ingestion.orchestration.cli run-forever --poll-interval-seconds 300

`run-once` does exactly one due-job scan and exits — the shape a cron
entry, systemd timer, or Kubernetes CronJob invokes. `run-daily` waits for
one configured local wall-clock time and runs the full ingestion and
maintenance cycle once per day. `run-forever` remains available for
deployments that prefer frequent due-job polling.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

import httpx

from packages.domain.tables import tenants
from services.alerts.webhook_delivery import retry_pending_deliveries
from services.analytics.opportunity_scoring import score_opportunities_for_tenant
from services.analytics.refresh import refresh_all_marts
from services.competitors.participation import backfill_winner_participations
from services.geospatial.config import GeocoderConfig
from services.geospatial.service import run_pending_jobs
from services.ingestion.connectors.ckan.scheduled import refresh_due_ckan_datasets

from .jobs import default_jobs
from .scheduler import run_due_jobs

DEFAULT_DAILY_AT = "02:30"
DEFAULT_DAILY_TIMEZONE = "Europe/Athens"


@dataclass(frozen=True)
class DailySchedule:
    at: time
    timezone: ZoneInfo


def parse_daily_schedule(at: str, timezone_name: str) -> DailySchedule:
    try:
        parsed_at = time.fromisoformat(at)
    except ValueError as exc:
        raise ValueError("--at must be HH:MM or HH:MM:SS") from exc
    if parsed_at.tzinfo is not None:
        raise ValueError("--at must be a local wall-clock time without an offset")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone {timezone_name!r}") from exc
    return DailySchedule(at=parsed_at, timezone=timezone)


def next_daily_run(now: datetime, schedule: DailySchedule) -> datetime:
    local_now = now.astimezone(schedule.timezone)
    candidate = datetime.combine(local_now.date(), schedule.at, tzinfo=schedule.timezone)
    if candidate <= local_now:
        candidate = datetime.combine(local_now.date() + timedelta(days=1), schedule.at, tzinfo=schedule.timezone)
    return candidate


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _run_once(
    database_url: str,
    raw_root: str,
    with_marts: bool,
    with_webhook_retries: bool = True,
    with_ckan_refresh: bool = True,
    with_geospatial: bool = True,
    with_competition: bool = True,
    with_opportunity_scoring: bool = True,
    geospatial_max_jobs: int = 500,
    scoring_lookback_days: int = 120,
    logical_timezone: str = DEFAULT_DAILY_TIMEZONE,
) -> None:
    jobs, skip_reasons = default_jobs(raw_root=raw_root)
    for reason in skip_reasons:
        print(reason)

    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            if jobs:
                outcomes = await run_due_jobs(conn, jobs, now=datetime.now(ZoneInfo(logical_timezone)))
                for outcome in outcomes:
                    job = outcome.job
                    label = f"{job.source_system}/{job.resource_type}/{job.partition_key}"
                    if outcome.ran:
                        print(f"{label}: ran {outcome.date_from}..{outcome.date_to} -> {outcome.result}")
                    elif outcome.error:
                        print(f"{label}: FAILED on {outcome.date_from}..{outcome.date_to} -> {outcome.error}")
                    else:
                        print(f"{label}: skipped ({outcome.skipped_reason})")
            else:
                print("no ingestion jobs configured")

            if with_ckan_refresh:
                try:
                    ckan_outcomes = await refresh_due_ckan_datasets(
                        conn, database_url=database_url, raw_root=raw_root
                    )
                    for ckan_outcome in ckan_outcomes:
                        label = f"CKAN/{ckan_outcome.adapter_name}/{ckan_outcome.catalog_dataset_id}"
                        if ckan_outcome.ran:
                            print(f"{label}: refreshed")
                        elif ckan_outcome.error:
                            print(f"{label}: FAILED -> {ckan_outcome.error}")
                        else:
                            print(f"{label}: skipped ({ckan_outcome.skipped_reason})")
                except Exception as exc:  # noqa: BLE001 - downstream stages must still run
                    await conn.rollback()
                    print(f"CKAN refresh sweep: FAILED -> {type(exc).__name__}: {exc}")

            if with_competition:
                try:
                    winner_seen, winner_inserted = await backfill_winner_participations(conn)
                    print(f"competition facts: considered={winner_seen} inserted={winner_inserted}")
                except Exception as exc:  # noqa: BLE001 - downstream stages must still run
                    await conn.rollback()
                    print(f"competition facts: FAILED -> {type(exc).__name__}: {exc}")

            if with_geospatial:
                try:
                    geocoder_config = GeocoderConfig.from_env()
                    geo_outcomes = await run_pending_jobs(
                        conn,
                        batch_size=geospatial_max_jobs,
                        geocoder_config=geocoder_config,
                    )
                    geo_counts: dict[str, int] = {}
                    for outcome in geo_outcomes:
                        geo_counts[outcome.status] = geo_counts.get(outcome.status, 0) + 1
                    print(f"geospatial queue: processed={len(geo_outcomes)} statuses={geo_counts}")
                except Exception as exc:  # noqa: BLE001 - downstream stages must still run
                    await conn.rollback()
                    print(f"geospatial queue: FAILED -> {type(exc).__name__}: {exc}")

            if with_marts:
                try:
                    mart_outcomes = await refresh_all_marts(conn)
                    if not mart_outcomes:
                        print("mart refresh already in progress elsewhere — skipped")
                    for outcome in mart_outcomes:
                        status = "OK" if outcome.succeeded else f"FAILED: {outcome.error}"
                        print(f"mart {outcome.mart_name}: {status}")
                except Exception as exc:  # noqa: BLE001 - scoring and delivery must still run
                    await conn.rollback()
                    print(f"mart refresh: FAILED -> {type(exc).__name__}: {exc}")

            if with_opportunity_scoring:
                try:
                    tenant_ids = [row.id for row in (await conn.execute(sa.select(tenants.c.id))).all()]
                    total_scores = 0
                    for tenant_id in tenant_ids:
                        score_result = await score_opportunities_for_tenant(
                            conn,
                            tenant_id=tenant_id,
                            lookback_days=scoring_lookback_days,
                        )
                        total_scores += score_result.scores_written
                    print(f"tenant opportunity scoring: tenants={len(tenant_ids)} scores={total_scores}")
                except Exception as exc:  # noqa: BLE001 - delivery must still run
                    await conn.rollback()
                    print(f"tenant opportunity scoring: FAILED -> {type(exc).__name__}: {exc}")

            if with_webhook_retries:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        retried = await retry_pending_deliveries(conn, client)
                    print(f"webhook delivery retries: processed={retried}")
                except Exception as exc:  # noqa: BLE001 - report without hiding the completed ingestion
                    await conn.rollback()
                    print(f"webhook delivery retries: FAILED -> {type(exc).__name__}: {exc}")
    finally:
        await engine.dispose()


async def _run_forever(
    database_url: str,
    raw_root: str,
    poll_interval_seconds: float,
    with_marts: bool,
    with_webhook_retries: bool = True,
    with_ckan_refresh: bool = True,
    logical_timezone: str = DEFAULT_DAILY_TIMEZONE,
) -> None:
    while True:
        await _run_once(
            database_url,
            raw_root,
            with_marts,
            with_webhook_retries,
            with_ckan_refresh,
            logical_timezone=logical_timezone,
        )
        await asyncio.sleep(poll_interval_seconds)


async def _run_daily(
    database_url: str,
    raw_root: str,
    schedule: DailySchedule,
    with_marts: bool,
    with_webhook_retries: bool,
    with_ckan_refresh: bool,
    with_geospatial: bool,
    with_competition: bool,
    with_opportunity_scoring: bool,
    geospatial_max_jobs: int,
    scoring_lookback_days: int,
    run_immediately: bool,
) -> None:
    if run_immediately:
        await _run_once(
            database_url,
            raw_root,
            with_marts,
            with_webhook_retries,
            with_ckan_refresh,
            with_geospatial,
            with_competition,
            with_opportunity_scoring,
            geospatial_max_jobs,
            scoring_lookback_days,
            str(schedule.timezone),
        )

    while True:
        now = datetime.now(schedule.timezone)
        next_run = next_daily_run(now, schedule)
        delay_seconds = max(0.0, (next_run - now).total_seconds())
        print(f"next daily ingestion: {next_run.isoformat()} ({delay_seconds:.0f}s)")
        await asyncio.sleep(delay_seconds)
        try:
            await _run_once(
                database_url,
                raw_root,
                with_marts,
                with_webhook_retries,
                with_ckan_refresh,
                with_geospatial,
                with_competition,
                with_opportunity_scoring,
                geospatial_max_jobs,
                scoring_lookback_days,
                str(schedule.timezone),
            )
        except Exception as exc:  # noqa: BLE001 - keep the unattended scheduler alive for the next day
            print(f"daily ingestion cycle: FAILED -> {type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("run-once", "run-daily", "run-forever"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
        sub.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))
        sub.add_argument("--no-marts", action="store_true", help="skip the analytics-mart refresh pass")
        sub.add_argument(
            "--no-webhook-retries", action="store_true", help="skip the pending-webhook-delivery retry sweep"
        )
        sub.add_argument(
            "--no-ckan-refresh", action="store_true", help="skip the due-check/refresh sweep over onboarded CKAN datasets"
        )
        sub.add_argument("--no-geospatial", action="store_true", help="skip processing queued geospatial jobs")
        sub.add_argument("--no-competition", action="store_true", help="skip winner/competition fact reconciliation")
        sub.add_argument(
            "--no-opportunity-scoring",
            action="store_true",
            help="skip recomputing tenant-relative opportunity scores",
        )
        sub.add_argument(
            "--geospatial-max-jobs",
            type=int,
            default=int(os.environ.get("DAILY_GEOSPATIAL_MAX_JOBS", "500")),
        )
        sub.add_argument(
            "--scoring-lookback-days",
            type=int,
            default=int(os.environ.get("DAILY_SCORING_LOOKBACK_DAYS", "120")),
        )
        sub.add_argument(
            "--timezone",
            default=os.environ.get("DAILY_INGEST_TIMEZONE", DEFAULT_DAILY_TIMEZONE),
            help="IANA timezone used for source date windows",
        )
        if name == "run-daily":
            sub.add_argument(
                "--at",
                default=os.environ.get("DAILY_INGEST_AT", DEFAULT_DAILY_AT),
                help="daily local wall-clock time (HH:MM)",
            )
            sub.add_argument(
                "--run-immediately",
                action="store_true",
                help="run once at startup before waiting for the configured time",
            )
        if name == "run-forever":
            sub.add_argument("--poll-interval-seconds", type=float, default=300.0)

    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.geospatial_max_jobs <= 0:
        parser.error("--geospatial-max-jobs must be positive")
    if args.scoring_lookback_days < 0:
        parser.error("--scoring-lookback-days must be non-negative")

    try:
        schedule = parse_daily_schedule(
            getattr(args, "at", DEFAULT_DAILY_AT),
            args.timezone,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.command == "run-once":
        asyncio.run(
            _run_once(
                args.database_url,
                args.raw_root,
                not args.no_marts,
                not args.no_webhook_retries,
                not args.no_ckan_refresh,
                not args.no_geospatial,
                not args.no_competition,
                not args.no_opportunity_scoring,
                args.geospatial_max_jobs,
                args.scoring_lookback_days,
                args.timezone,
            )
        )
    elif args.command == "run-daily":
        asyncio.run(
            _run_daily(
                args.database_url,
                args.raw_root,
                schedule,
                not args.no_marts,
                not args.no_webhook_retries,
                not args.no_ckan_refresh,
                not args.no_geospatial,
                not args.no_competition,
                not args.no_opportunity_scoring,
                args.geospatial_max_jobs,
                args.scoring_lookback_days,
                args.run_immediately,
            )
        )
    elif args.command == "run-forever":
        asyncio.run(
            _run_forever(
                args.database_url,
                args.raw_root,
                args.poll_interval_seconds,
                not args.no_marts,
                not args.no_webhook_retries,
                not args.no_ckan_refresh,
                args.timezone,
            )
        )


if __name__ == "__main__":
    main()
