#!/usr/bin/env python3
"""Resumable, rate-conscious TED backfill for European market cohorts.

Example:
    DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel \
    python scripts/backfill_ted_europe.py \
      --date-from 2026-06-01 --date-to 2026-06-30 \
      --countries GR,PT,ES,IT,CY --window-days 1 --continue-on-error
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.intelligence.eu_matching import EU_MEMBER_COUNTRIES, date_windows  # noqa: E402


def _asyncpg_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value[len("postgresql://") :]
    return value


async def _resume_date(conn, *, resume_key: str, country: str) -> date | None:
    from packages.domain.tables import source_cursors

    row = (
        await conn.execute(
            sa.select(source_cursors.c.cursor_value).where(
                source_cursors.c.source_system == "TED_EU_BACKFILL",
                source_cursors.c.resource_type == resume_key,
                source_cursors.c.partition_key == country,
            )
        )
    ).first()
    if not row or not row.cursor_value.get("last_ingested_date"):
        return None
    return date.fromisoformat(row.cursor_value["last_ingested_date"]) + timedelta(days=1)


async def _run(args: argparse.Namespace) -> None:
    countries = tuple(
        dict.fromkeys(value.strip().upper() for value in args.countries.split(",") if value.strip())
    )
    unknown = sorted(set(countries) - set(EU_MEMBER_COUNTRIES))
    if unknown:
        raise SystemExit(f"Unsupported EU country code(s): {', '.join(unknown)}")
    if args.dry_run:
        windows = date_windows(args.date_from, args.date_to, args.window_days)
        print(
            f"TED EU backfill: countries={len(countries)} windows/country={len(windows)} "
            f"range={args.date_from}..{args.date_to} rate={args.rate}/min"
        )
        return

    from packages.domain.tables import source_cursors
    from services.ingestion.connectors.ted.scheduled import run_scheduled_window
    from services.intelligence.eu_benchmarking import (
        refresh_all_cross_border_matches,
        refresh_eu_benchmark_snapshots,
    )

    os.environ["TED_RATE_LIMIT_PER_MINUTE"] = str(args.rate)
    engine = create_async_engine(_asyncpg_url(args.database_url), pool_pre_ping=True)
    totals = {"seen": 0, "upserted": 0, "failed": 0}
    try:
        for country in countries:
            async with engine.connect() as conn:
                resume_from = None if args.restart else await _resume_date(
                    conn,
                    resume_key=args.resume_key,
                    country=country,
                )
            start = max(args.date_from, resume_from) if resume_from else args.date_from
            if start > args.date_to:
                print(f"TED/{country}: already complete")
                continue
            for window_from, window_to in date_windows(start, args.date_to, args.window_days):
                try:
                    async with engine.begin() as conn:
                        result = await run_scheduled_window(
                            conn,
                            window_from,
                            window_to,
                            country=country,
                            raw_root=args.raw_root,
                        )
                        if result.get("records_failed") and not args.continue_on_error:
                            raise RuntimeError(
                                f"{result['records_failed']} TED records failed; rerun with "
                                "--continue-on-error only after inspecting the errors"
                            )
                        await conn.execute(
                            pg_insert(source_cursors)
                            .values(
                                source_system="TED_EU_BACKFILL",
                                resource_type=args.resume_key,
                                partition_key=country,
                                cursor_value={"last_ingested_date": window_to.isoformat()},
                                last_success_at=datetime.now(timezone.utc),
                                last_attempt_at=datetime.now(timezone.utc),
                                last_error=None,
                            )
                            .on_conflict_do_update(
                                index_elements=[
                                    source_cursors.c.source_system,
                                    source_cursors.c.resource_type,
                                    source_cursors.c.partition_key,
                                ],
                                set_={
                                    "cursor_value": {"last_ingested_date": window_to.isoformat()},
                                    "last_success_at": datetime.now(timezone.utc),
                                    "last_attempt_at": datetime.now(timezone.utc),
                                    "last_error": None,
                                },
                            )
                        )
                    totals["seen"] += int(result.get("records_fetched", 0))
                    totals["upserted"] += int(result.get("records_upserted", 0))
                    totals["failed"] += int(result.get("records_failed", 0))
                    print(
                        f"TED/{country} {window_from}..{window_to}: "
                        f"seen={result.get('records_fetched', 0)} "
                        f"upserted={result.get('records_upserted', 0)} "
                        f"failed={result.get('records_failed', 0)}"
                    )
                except Exception as exc:  # noqa: BLE001 - operator chooses continuation policy
                    print(
                        f"TED/{country} {window_from}..{window_to}: "
                        f"FAILED {type(exc).__name__}: {exc}"
                    )
                    if not args.continue_on_error:
                        raise
                if args.pause_seconds:
                    await asyncio.sleep(args.pause_seconds)

        async with engine.begin() as conn:
            cohorts = await refresh_eu_benchmark_snapshots(
                conn,
                date_from=args.date_from,
                date_to=args.date_to,
            )
            tenant_runs = await refresh_all_cross_border_matches(conn)
        print(
            f"complete: {totals} cohorts={cohorts} "
            f"tenant_matches={sum(run.matches_written for run in tenant_runs)}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--date-to", type=date.fromisoformat, required=True)
    parser.add_argument("--countries", default=",".join(EU_MEMBER_COUNTRIES))
    parser.add_argument("--window-days", type=int, default=1)
    parser.add_argument("--rate", type=float, default=60)
    parser.add_argument("--pause-seconds", type=float, default=0.25)
    parser.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))
    parser.add_argument("--resume-key", default="EU_MARKET_V1")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.database_url and not args.dry_run:
        parser.error("--database-url or $DATABASE_URL is required")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
