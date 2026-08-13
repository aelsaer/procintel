#!/usr/bin/env python3
"""Refresh Διαύγεια organization, unit, signer and version dictionaries."""

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

from packages.source_clients.raw_store import configured_raw_store  # noqa: E402
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient  # noqa: E402
from services.ingestion.connectors.diavgeia.config import (  # noqa: E402
    DiavgeiaConnectorConfig,
)
from services.ingestion.connectors.diavgeia.resolve import (  # noqa: E402
    backfill_decision_references,
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _database_name(url: str) -> str:
    return urlparse(url.replace("+asyncpg", "")).path.rsplit("/", 1)[-1]


async def _run(args: argparse.Namespace) -> dict[str, int]:
    engine = create_async_engine(_async_url(args.database_url))
    client = DiavgeiaClient(DiavgeiaConnectorConfig.from_env())
    raw_store = configured_raw_store(args.raw_root)
    try:
        async with engine.connect() as conn:
            return await backfill_decision_references(
                conn,
                client=client,
                raw_store=raw_store,
                limit=args.limit,
                process_documents=args.with_documents,
            )
    finally:
        await client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--raw-root",
        default=os.environ.get("RAW_STORE_ROOT", "./raw"),
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--with-documents", action="store_true")
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
    print(
        json.dumps(
            asyncio.run(_run(args)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
