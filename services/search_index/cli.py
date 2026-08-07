"""Manual CLI entrypoint.

    python -m services.search_index.cli create-index
    python -m services.search_index.cli reindex-all
    python -m services.search_index.cli reindex-catalogs

The full rebuild is an explicit recovery/maintenance command. Incremental
indexing is already part of scheduled ΚΗΜΔΗΣ and TED ingestion.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from .client import create_index as os_create_index
from .client import index_exists
from .catalog import reindex_catalogs
from .config import OpenSearchConfig
from .indexer import rebuild_all_indexes_atomic
from .mapping import PROCUREMENT_ACTS_MAPPING


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _create_index() -> None:
    config = OpenSearchConfig.from_env()
    async with httpx.AsyncClient(timeout=config.request_timeout_seconds) as client:
        if await index_exists(client, config):
            print(f"index {config.index_name!r} already exists — nothing to do")
            return
        await os_create_index(client, config, PROCUREMENT_ACTS_MAPPING)
        print(f"created index {config.index_name!r}")


async def _reindex_all(database_url: str) -> None:
    config = OpenSearchConfig.from_env()
    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn, httpx.AsyncClient(timeout=config.request_timeout_seconds) as client:
            result = await rebuild_all_indexes_atomic(conn, client, config)
            print(
                f"indexed {result.acts_indexed} acts into {config.index_name!r}; "
                f"catalogs={result.catalogs}; build={result.build_id}"
            )
    finally:
        await engine.dispose()


async def _reindex_catalogs(database_url: str) -> None:
    config = OpenSearchConfig.from_env()
    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with (
            engine.connect() as conn,
            httpx.AsyncClient(
                timeout=config.request_timeout_seconds
            ) as client,
        ):
            result = await reindex_catalogs(conn, client, config)
            print(f"indexed catalogs={result.counts}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-index")
    reindex_parser = subparsers.add_parser("reindex-all")
    reindex_parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    catalog_parser = subparsers.add_parser("reindex-catalogs")
    catalog_parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
    )

    args = parser.parse_args()
    if args.command == "create-index":
        asyncio.run(_create_index())
    elif args.command == "reindex-all":
        if not args.database_url:
            parser.error("--database-url or $DATABASE_URL is required")
        asyncio.run(_reindex_all(args.database_url))
    elif args.command == "reindex-catalogs":
        if not args.database_url:
            parser.error("--database-url or $DATABASE_URL is required")
        asyncio.run(_reindex_catalogs(args.database_url))


if __name__ == "__main__":
    main()
