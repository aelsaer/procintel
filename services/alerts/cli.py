"""Manual/scheduled CLI entrypoint.

    python -m services.alerts.cli retry-webhooks
    python -m services.alerts.cli send-digests

Sweeps `webhook_deliveries` for rows still PENDING and due
(`next_retry_at` in the past) and retries them — the shape a cron entry,
systemd timer, or Kubernetes CronJob invokes on its own schedule,
independent of ingestion. Also invoked automatically as part of
`services.ingestion.orchestration.cli run-once`/`run-forever` unless
`--no-webhook-retries` is passed there.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from .digests import process_due_digests
from .factory import build_delivery_channel
from .webhook_delivery import retry_pending_deliveries


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _retry(database_url: str) -> None:
    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn, httpx.AsyncClient(timeout=10.0) as client:
            retried = await retry_pending_deliveries(conn, client)
            print(f"retried {retried} pending webhook deliveries")
    finally:
        await engine.dispose()


async def _send_digests(database_url: str) -> None:
    engine = create_async_engine(_to_asyncpg_url(database_url))
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            async with engine.connect() as conn:
                result = await process_due_digests(
                    conn,
                    delivery_channel=build_delivery_channel(client),
                )
                print(
                    f"checked {result.rules_checked} rules; "
                    f"created {result.digests_created} digests with "
                    f"{result.events_included} events"
                )
        finally:
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    retry_parser = subparsers.add_parser("retry-webhooks")
    retry_parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    digest_parser = subparsers.add_parser("send-digests")
    digest_parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))

    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")

    if args.command == "retry-webhooks":
        asyncio.run(_retry(args.database_url))
    elif args.command == "send-digests":
        asyncio.run(_send_digests(args.database_url))


if __name__ == "__main__":
    main()
