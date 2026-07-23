"""Manual/scheduled CLI entrypoint.

    python -m services.analytics.cli refresh-marts

Runs `refresh.py::refresh_all_marts` once and exits — the shape a cron
entry, systemd timer, or Kubernetes CronJob invokes on its own schedule
(independent of ingestion — a mart refresh is worth running even on a run
where no connector job happened to be due). Also invoked automatically as
part of `services.ingestion.orchestration.cli run-once`/`run-forever`
unless `--no-marts` is passed there.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import tenants

from .opportunity_scoring import score_opportunities_for_tenant
from .refresh import refresh_all_marts
from .scoring_worker import process_next_scoring_job


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _refresh(database_url: str) -> None:
    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            outcomes = await refresh_all_marts(conn)
            if not outcomes:
                print("mart refresh already in progress elsewhere — skipped")
                return
            for outcome in outcomes:
                status = "OK" if outcome.succeeded else f"FAILED: {outcome.error}"
                print(f"{outcome.mart_name}: {status}")
    finally:
        await engine.dispose()


async def _score_opportunities(
    database_url: str,
    *,
    tenant_id: str | None,
    all_tenants: bool,
    lookback_days: int,
    include_contracted: bool,
    limit: int | None,
) -> None:
    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            if all_tenants:
                tenant_ids = [row.id for row in (await conn.execute(tenants.select())).all()]
            elif tenant_id:
                import uuid

                tenant_ids = [uuid.UUID(tenant_id)]
            else:
                raise ValueError("--tenant-id or --all-tenants is required")

            for tid in tenant_ids:
                result = await score_opportunities_for_tenant(
                    conn,
                    tenant_id=tid,
                    lookback_days=lookback_days,
                    include_contracted=include_contracted,
                    limit=limit,
                )
                print(
                    f"tenant {tid}: rules={result.rules_considered} "
                    f"candidates={result.candidates_seen} scores={result.scores_written}"
                )
    finally:
        await engine.dispose()


async def _score_queued(database_url: str) -> None:
    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            processed = 0
            while True:
                result = await process_next_scoring_job(conn)
                if result is None:
                    break
                processed += 1
                print(f"tenant {result.tenant_id}: candidates={result.candidates_seen} scores={result.scores_written}")
            print(f"processed scoring jobs: {processed}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    refresh_parser = subparsers.add_parser("refresh-marts")
    refresh_parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))

    score_parser = subparsers.add_parser("score-opportunities")
    score_parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    score_target = score_parser.add_mutually_exclusive_group(required=True)
    score_target.add_argument("--tenant-id")
    score_target.add_argument("--all-tenants", action="store_true")
    score_parser.add_argument("--lookback-days", type=int, default=120)
    score_parser.add_argument("--include-contracted", action="store_true")
    score_parser.add_argument("--limit", type=int, default=None)

    queued_parser = subparsers.add_parser("score-queued")
    queued_parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))

    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")

    if args.command == "refresh-marts":
        asyncio.run(_refresh(args.database_url))
    elif args.command == "score-opportunities":
        asyncio.run(
            _score_opportunities(
                args.database_url,
                tenant_id=args.tenant_id,
                all_tenants=args.all_tenants,
                lookback_days=args.lookback_days,
                include_contracted=args.include_contracted,
                limit=args.limit,
            )
        )
    elif args.command == "score-queued":
        asyncio.run(_score_queued(args.database_url))


if __name__ == "__main__":
    main()
