"""Run pending export jobs outside the API process."""

from __future__ import annotations

import argparse
import asyncio
import os

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import export_jobs

from .generate import _async_url, process_export_job


async def _run(database_url: str, limit: int) -> None:
    engine = create_async_engine(_async_url(database_url))
    processed = 0
    try:
        async with engine.connect() as conn:
            ids = (
                await conn.execute(
                    sa.select(export_jobs.c.id)
                    .where(export_jobs.c.status.in_(("PENDING", "FAILED")))
                    .order_by(export_jobs.c.created_at)
                    .limit(limit)
                )
            ).scalars().all()
            for job_id in ids:
                await process_export_job(conn, job_id)
                processed += 1
    finally:
        await engine.dispose()
    print(f"processed {processed} export jobs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.limit < 1:
        parser.error("--limit must be positive")
    asyncio.run(_run(args.database_url, args.limit))


if __name__ == "__main__":
    main()
