#!/usr/bin/env python
"""Refresh evidence-backed early-demand signals from canonical records."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date

from sqlalchemy.ext.asyncio import create_async_engine

from services.intelligence.pre_tender import refresh_derived_procurement_signals


def _async_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _run(database_url: str, as_of: date | None) -> None:
    engine = create_async_engine(_async_url(database_url))
    try:
        async with engine.connect() as conn:
            result = await refresh_derived_procurement_signals(conn, as_of=as_of)
        print(f"early_requests={result.early_requests} expiring_contracts={result.expiring_contracts}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--as-of", type=date.fromisoformat)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    asyncio.run(_run(args.database_url, args.as_of))


if __name__ == "__main__":
    main()
