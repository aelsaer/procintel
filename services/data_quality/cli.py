"""Run automated procurement data-quality checks."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date

from sqlalchemy.ext.asyncio import create_async_engine

from .service import run_data_quality_checks


def _asyncpg_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def _run(database_url: str, date_from: date | None, date_to: date | None) -> None:
    engine = create_async_engine(_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            result = await run_data_quality_checks(
                conn,
                date_from=date_from,
                date_to=date_to,
            )
            print(
                f"data quality: opened={result.issues_opened} "
                f"resolved={result.issues_resolved} "
                f"invalid_dates_repaired={result.invalid_dates_repaired} "
                f"by_code={result.by_code}"
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--date-from", type=date.fromisoformat)
    parser.add_argument("--date-to", type=date.fromisoformat)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.date_from and args.date_to and args.date_from > args.date_to:
        parser.error("--date-from must be before or equal to --date-to")
    asyncio.run(_run(args.database_url, args.date_from, args.date_to))


if __name__ == "__main__":
    main()
