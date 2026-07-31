#!/usr/bin/env python3
"""Drain selected durable enrichment providers without re-running ingestion."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.ingestion.connectors.anaptyxi.config import (  # noqa: E402
    SUPPORTED_PROGRAM_PERIODS,
)
from services.ingestion.enrichment_reconciliation import (  # noqa: E402
    enqueue_process_diavgeia_search_jobs,
)
from services.ingestion.enrichment_worker import (  # noqa: E402
    run_pending_enrichment_jobs,
)

KNOWN_PROVIDERS = {
    "KHMDHS_DOCUMENT",
    "KHMDHS_ADAMCHAIN",
    "DIAVGEIA",
    "DIAVGEIA_SEARCH",
    "GEMI",
    "MEF",
    "OPENSEARCH",
    *SUPPORTED_PROGRAM_PERIODS,
}


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _database_name(url: str) -> str:
    return urlparse(url.replace("+asyncpg", "")).path.rsplit("/", 1)[-1]


def _budget(value: str) -> tuple[str, int]:
    provider, separator, raw_limit = value.partition("=")
    if not separator or provider not in KNOWN_PROVIDERS:
        raise argparse.ArgumentTypeError("budget must be PROVIDER=LIMIT")
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("budget limit must be an integer") from exc
    if limit < 0:
        raise argparse.ArgumentTypeError("budget limit must be non-negative")
    return provider, limit


async def _run(args: argparse.Namespace) -> dict:
    engine = create_async_engine(_async_url(args.database_url))
    try:
        async with engine.connect() as conn:
            reconciled = (
                await enqueue_process_diavgeia_search_jobs(conn)
                if args.reconcile_diavgeia
                else 0
            )
            result = await run_pending_enrichment_jobs(
                conn,
                raw_root=args.raw_root,
                limit=args.limit,
                providers=set(args.provider),
                provider_budgets=dict(args.budget),
            )
            return {
                "diavgeia_search_jobs_reconciled": reconciled,
                "result": {
                    "claimed": result.claimed,
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "blocked_config": result.blocked_config,
                    "blocked_upstream": result.blocked_upstream,
                    "deferred": result.deferred,
                    "by_provider": result.by_provider,
                },
            }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))
    parser.add_argument("--provider", action="append", choices=sorted(KNOWN_PROVIDERS), required=True)
    parser.add_argument("--budget", action="append", type=_budget, default=[])
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--reconcile-diavgeia", action="store_true")
    parser.add_argument("--allow-non-isolated-database", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.limit < 0:
        parser.error("--limit must be non-negative")
    if (
        "slice" not in _database_name(args.database_url).casefold()
        and not args.allow_non_isolated_database
    ):
        parser.error("refusing non-isolated database without explicit override")
    missing_budgets = set(args.provider).difference(dict(args.budget))
    if missing_budgets:
        parser.error(
            "missing --budget for: " + ", ".join(sorted(missing_budgets))
        )
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
