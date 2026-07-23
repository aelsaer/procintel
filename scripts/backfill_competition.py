#!/usr/bin/env python3
"""Build competitor evidence from data already stored in PostgreSQL.

This script makes no provider calls and therefore consumes no external API
quota. It records official suppliers/contractors as winners and scans stored
document pages for explicit participant-role + ΑΦΜ evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.competitors.participation import (  # noqa: E402
    backfill_winner_participations,
    load_document_pages_for_participation_backfill,
    record_document_participant,
)
from services.documents.entities import extract_procurement_participants  # noqa: E402


def _asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _run(database_url: str) -> None:
    engine = create_async_engine(_asyncpg_url(database_url))
    winner_seen = winner_inserted = document_seen = document_inserted = 0
    try:
        async with engine.begin() as conn:
            winner_seen, winner_inserted = await backfill_winner_participations(conn)
            for page in await load_document_pages_for_participation_backfill(conn):
                for participant in extract_procurement_participants(page.text):
                    document_seen += 1
                    document_inserted += int(
                        await record_document_participant(
                            conn,
                            act_id=page.act_id,
                            document_id=page.document_id,
                            source_record_id=page.source_record_id,
                            source_page=page.page_number,
                            participant=participant,
                            confidence_scale=float(page.confidence_scale),
                        )
                    )
    finally:
        await engine.dispose()
    print(f"official winners: seen={winner_seen} inserted={winner_inserted}")
    print(f"document participants: seen={document_seen} inserted={document_inserted}")
    print(f"total inserted: {winner_inserted + document_inserted}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    asyncio.run(_run(args.database_url))


if __name__ == "__main__":
    main()

