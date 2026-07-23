"""Geospatial enrichment worker.

Examples:
  python -m services.geospatial.cli enqueue-existing --limit 10000
  python -m services.geospatial.cli worker --once --batch-size 100
  python -m services.geospatial.cli worker --poll-interval-seconds 15
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from .config import GeocoderConfig
from .geonames import (
    DEFAULT_ADMIN1_URL,
    DEFAULT_ADMIN2_URL,
    DEFAULT_GEONAMES_GR_URL,
    replace_place_gazetteer,
)
from .service import enqueue_existing_acts, run_pending_jobs


def _asyncpg_url(url: str) -> str:
    return "postgresql+asyncpg://" + url[len("postgresql://") :] if url.startswith("postgresql://") else url


async def _worker(database_url: str, *, once: bool, batch_size: int, poll_interval: float) -> None:
    config = GeocoderConfig.from_env()
    engine = create_async_engine(_asyncpg_url(database_url))
    try:
        while True:
            async with engine.connect() as conn:
                outcomes = await run_pending_jobs(conn, batch_size=batch_size, geocoder_config=config)
            if outcomes:
                counts: dict[str, int] = {}
                for outcome in outcomes:
                    counts[outcome.status] = counts.get(outcome.status, 0) + 1
                print(f"processed {len(outcomes)} geospatial jobs: {counts}")
            elif once:
                print("no geospatial jobs due")
            if once:
                break
            await asyncio.sleep(poll_interval)
    finally:
        await engine.dispose()


async def _enqueue(
    database_url: str,
    *,
    limit: int | None,
    requeue_partial: bool,
    requeue_all: bool,
) -> None:
    engine = create_async_engine(_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            affected = await enqueue_existing_acts(
                conn,
                limit=limit,
                requeue_partial=requeue_partial,
                requeue_all=requeue_all,
            )
            print(f"considered {affected} existing acts for geospatial enrichment")
    finally:
        await engine.dispose()


async def _load_gazetteer(
    database_url: str,
    *,
    country: str,
    url: str,
    admin1_url: str,
    admin2_url: str,
    raw_root: str,
) -> None:
    async with httpx.AsyncClient(timeout=60.0, headers={"User-Agent": "Procintel/0.1 GeoNames gazetteer loader"}) as client:
        responses = await asyncio.gather(client.get(url), client.get(admin1_url), client.get(admin2_url))
        for response in responses:
            response.raise_for_status()
        payload, admin1_payload, admin2_payload = (response.content for response in responses)

    digest = hashlib.sha256(payload).hexdigest()
    ingestion_date = datetime.now(timezone.utc).date().isoformat()
    path = Path(raw_root) / "geonames" / "places" / f"ingestion_date={ingestion_date}" / f"country={country.upper()}" / f"{digest}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(payload)

    engine = create_async_engine(_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            rows = await replace_place_gazetteer(
                conn,
                country_code=country,
                country_payload=payload,
                admin1_payload=admin1_payload,
                admin2_payload=admin2_payload,
            )
            print(f"loaded {rows} {country.upper()} GeoNames places from {url} ({path})")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    worker = subparsers.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--batch-size", type=int, default=50)
    worker.add_argument("--poll-interval-seconds", type=float, default=15.0)

    enqueue = subparsers.add_parser("enqueue-existing")
    enqueue.add_argument("--limit", type=int)
    enqueue.add_argument("--requeue-partial", action="store_true")
    enqueue.add_argument("--requeue-all", action="store_true", help="rerun even successful jobs after gazetteer/parser changes")

    gazetteer = subparsers.add_parser("load-place-gazetteer")
    gazetteer.add_argument("--country", default="GR")
    gazetteer.add_argument("--url", default=DEFAULT_GEONAMES_GR_URL)
    gazetteer.add_argument("--admin1-url", default=DEFAULT_ADMIN1_URL)
    gazetteer.add_argument("--admin2-url", default=DEFAULT_ADMIN2_URL)
    gazetteer.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))

    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.command == "worker":
        if args.batch_size <= 0 or args.poll_interval_seconds <= 0:
            parser.error("worker sizes and intervals must be positive")
        asyncio.run(
            _worker(
                args.database_url,
                once=args.once,
                batch_size=args.batch_size,
                poll_interval=args.poll_interval_seconds,
            )
        )
    elif args.command == "enqueue-existing":
        if args.limit is not None and args.limit <= 0:
            parser.error("--limit must be positive")
        asyncio.run(
            _enqueue(
                args.database_url,
                limit=args.limit,
                requeue_partial=args.requeue_partial,
                requeue_all=args.requeue_all,
            )
        )
    else:
        asyncio.run(
            _load_gazetteer(
                args.database_url,
                country=args.country,
                url=args.url,
                admin1_url=args.admin1_url,
                admin2_url=args.admin2_url,
                raw_root=args.raw_root,
            )
        )


if __name__ == "__main__":
    main()
