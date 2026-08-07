#!/usr/bin/env python3
"""Backfill one calendar month with safe provider budgets.

Default target is June 2026, because the product is currently being prepared
in July 2026 and the first useful historical slice is "last month".

Example:
    DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel \
    python scripts/backfill_month.py \
      --year 2026 --month 6 \
      --window-days 1 \
      --khmdhs-rate 180 \
      --continue-on-error

Smoke run:
    python scripts/backfill_month.py --dry-run
    python scripts/backfill_month.py --resource notice --max-pages-per-window 1 --max-records-per-window 25
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import os
import sys
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.analytics.opportunity_scoring import (  # noqa: E402
    score_opportunities_for_tenant,
    tenant_ids_with_business_profiles,
)
from services.analytics.refresh import refresh_all_marts  # noqa: E402
from services.ingestion.connectors.anaptyxi.config import DEFAULT_PROGRAM_PERIOD  # noqa: E402
from services.ingestion.connectors.khmdhs.client import ALL_RESOURCES  # noqa: E402
from services.ingestion.connectors.khmdhs.cli import _run_backfill as run_khmdhs_backfill  # noqa: E402
from services.ingestion.connectors.ted.cli import _run_backfill as run_ted_backfill  # noqa: E402

DEFAULT_RESOURCE_ORDER = ("notice", "auction", "contract", "payment", "request")


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _has_env(*names: str) -> bool:
    return all(bool(os.environ.get(name)) for name in names)


def _set_rate_env(args: argparse.Namespace) -> None:
    mapping = {
        "khmdhs_rate": "KHMDHS_RATE_LIMIT_PER_MINUTE",
        "diavgeia_rate": "DIAVGEIA_RATE_LIMIT_PER_MINUTE",
        "gemi_rate": "GEMI_RATE_LIMIT_PER_MINUTE",
        "anaptyxi_rate": "ANAPTYXI_RATE_LIMIT_PER_MINUTE",
        "mef_rate": "MEF_RATE_LIMIT_PER_MINUTE",
        "ted_rate": "TED_RATE_LIMIT_PER_MINUTE",
        "vies_rate": "VIES_RATE_LIMIT_PER_MINUTE",
    }
    for arg_name, env_name in mapping.items():
        value = getattr(args, arg_name)
        if value is not None:
            os.environ[env_name] = str(value)


def _resolve_provider_flags(args: argparse.Namespace) -> dict[str, bool]:
    flags = {
        "diavgeia": not args.no_diavgeia,
        "gemi": False if args.no_gemi else _has_env("GEMI_API_KEY"),
        "anaptyxi": False
        if args.no_anaptyxi
        else (
            _has_env("ANAPTYXI_API_BASE_URL")
            or _has_env(f"{args.anaptyxi_period}_API_BASE_URL")
        ),
        "mef": not args.no_mef,
        "ted": not args.no_ted,
        "vies": args.with_vies,
        "opensearch": False if args.no_opensearch else _has_env("OPENSEARCH_URL"),
    }
    required = {
        "gemi": args.require_gemi,
        "anaptyxi": args.require_anaptyxi,
        "mef": args.require_mef,
        "ted": args.require_ted,
    }
    missing_required = [name for name, required_flag in required.items() if required_flag and not flags[name]]
    if missing_required:
        raise SystemExit(f"Required provider(s) are not configured: {', '.join(missing_required)}")
    return flags


def _print_plan(
    *,
    date_from: date,
    date_to: date,
    resources: list[str],
    flags: dict[str, bool],
    args: argparse.Namespace,
) -> None:
    print("Backfill plan")
    print(f"  date range: {date_from}..{date_to}")
    print(f"  resources: {', '.join(resources)}")
    print(f"  window_days: {args.window_days}")
    print(f"  resume_key: {args.resume_key}")
    print(f"  max_pages_per_window: {args.max_pages_per_window}")
    print(f"  max_records_per_window: {args.max_records_per_window}")
    print("  providers:")
    for name, enabled in flags.items():
        print(f"    {name}: {'on' if enabled else 'off'}")


async def _refresh_and_score(args: argparse.Namespace) -> None:
    engine = create_async_engine(_to_asyncpg_url(args.database_url))
    try:
        async with engine.connect() as conn:
            if not args.no_marts:
                outcomes = await refresh_all_marts(conn)
                if not outcomes:
                    print("analytics marts: skipped, another refresh is running")
                for outcome in outcomes:
                    status = "OK" if outcome.succeeded else f"FAILED: {outcome.error}"
                    print(f"analytics mart {outcome.mart_name}: {status}")

            if not args.no_score_opportunities:
                if args.score_tenant_id:
                    tenant_ids = [uuid.UUID(args.score_tenant_id)]
                else:
                    tenant_ids = await tenant_ids_with_business_profiles(conn)
                for tenant_id in tenant_ids:
                    result = await score_opportunities_for_tenant(
                        conn,
                        tenant_id=tenant_id,
                        lookback_days=args.score_lookback_days,
                        include_contracted=args.score_include_contracted,
                        limit=args.score_limit,
                    )
                    print(
                        f"opportunity_scores tenant={tenant_id}: rules={result.rules_considered} "
                        f"candidates={result.candidates_seen} written={result.scores_written}"
                    )
    finally:
        await engine.dispose()


async def _run(args: argparse.Namespace) -> None:
    _set_rate_env(args)
    date_from, date_to = (
        (args.date_from, args.date_to) if args.date_from and args.date_to else _month_bounds(args.year, args.month)
    )
    if args.resume_key is None:
        args.resume_key = f"bootstrap-{date_from:%Y-%m}"
    resources = args.resource or list(DEFAULT_RESOURCE_ORDER)
    flags = _resolve_provider_flags(args)
    flags["documents"] = args.with_documents
    _print_plan(date_from=date_from, date_to=date_to, resources=resources, flags=flags, args=args)
    if args.dry_run:
        return

    totals = await run_khmdhs_backfill(
        resources=resources,
        date_from=date_from,
        date_to=date_to,
        database_url=args.database_url,
        raw_root=args.raw_root,
        resolve_adam_chains=True,
        fire_alerts=not args.no_alerts,
        with_diavgeia=flags["diavgeia"],
        with_diavgeia_search=args.with_diavgeia_search,
        with_documents=args.with_documents,
        with_gemi=flags["gemi"],
        with_anaptyxi=flags["anaptyxi"],
        anaptyxi_period=args.anaptyxi_period,
        with_mef=flags["mef"],
        with_opensearch=flags["opensearch"],
        window_days=args.window_days,
        max_pages_per_window=args.max_pages_per_window,
        max_records_per_window=args.max_records_per_window,
        resume_key=args.resume_key,
        continue_on_error=args.continue_on_error,
    )
    print(f"KHMDHS totals: {totals}")

    if flags["ted"]:
        await run_ted_backfill(
            country="GR",
            date_from=date_from,
            date_to=date_to,
            database_url=args.database_url,
            raw_root=args.raw_root,
            with_vies=flags["vies"],
        )

    await _refresh_and_score(args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=6)
    parser.add_argument("--date-from", type=date.fromisoformat, default=None)
    parser.add_argument("--date-to", type=date.fromisoformat, default=None)
    parser.add_argument("--resource", action="append", choices=sorted(ALL_RESOURCES), default=None)
    parser.add_argument("--window-days", type=int, default=1)
    parser.add_argument("--resume-key", default=None)
    parser.add_argument("--max-pages-per-window", type=int, default=None)
    parser.add_argument("--max-records-per-window", type=int, default=None)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")

    parser.add_argument("--khmdhs-rate", type=float, default=None)
    parser.add_argument("--diavgeia-rate", type=float, default=None)
    parser.add_argument("--gemi-rate", type=float, default=None)
    parser.add_argument("--anaptyxi-rate", type=float, default=None)
    parser.add_argument("--mef-rate", type=float, default=None)
    parser.add_argument("--ted-rate", type=float, default=None)
    parser.add_argument("--vies-rate", type=float, default=None)

    parser.add_argument("--no-alerts", action="store_true")
    parser.add_argument("--no-diavgeia", action="store_true")
    parser.add_argument("--with-diavgeia-search", action="store_true")
    parser.add_argument(
        "--with-documents",
        action="store_true",
        help="download/OCR each linked Διαύγεια decision's own PDF (§23) — "
        "requires Διαύγεια to be enabled (the default); heavier per-document "
        "cost (download, antivirus scan, OCR), off by default",
    )
    parser.add_argument("--no-gemi", action="store_true")
    parser.add_argument("--require-gemi", action="store_true")
    parser.add_argument("--no-anaptyxi", action="store_true")
    parser.add_argument("--require-anaptyxi", action="store_true")
    parser.add_argument("--anaptyxi-period", default=DEFAULT_PROGRAM_PERIOD)
    parser.add_argument("--no-mef", action="store_true")
    parser.add_argument("--require-mef", action="store_true")
    parser.add_argument("--no-ted", action="store_true")
    parser.add_argument("--require-ted", action="store_true")
    parser.add_argument("--with-vies", action="store_true")
    parser.add_argument("--no-opensearch", action="store_true")
    parser.add_argument("--no-marts", action="store_true")
    parser.add_argument("--no-score-opportunities", action="store_true")
    parser.add_argument("--score-tenant-id", default=None)
    parser.add_argument("--score-lookback-days", type=int, default=120)
    parser.add_argument("--score-include-contracted", action="store_true")
    parser.add_argument("--score-limit", type=int, default=None)
    args = parser.parse_args()

    if not args.database_url and not args.dry_run:
        parser.error("--database-url or $DATABASE_URL is required")
    if (args.date_from is None) != (args.date_to is None):
        parser.error("--date-from and --date-to must be passed together")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
