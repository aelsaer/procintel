#!/usr/bin/env python3
"""Download and extract missing ΚΗΜΔΗΣ documents from canonical acts."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.domain.tables import act_identifiers, documents, procurement_acts, source_records  # noqa: E402
from packages.source_clients.rate_limit import TokenBucket  # noqa: E402
from services.ingestion.connectors.khmdhs.client import ALL_RESOURCES  # noqa: E402
from services.ingestion.connectors.khmdhs.documents import (  # noqa: E402
    khmdhs_document_type,
    process_khmdhs_attachment,
)


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _run(args: argparse.Namespace) -> None:
    engine = create_async_engine(_to_asyncpg_url(args.database_url))
    rate_limiter = TokenBucket(args.rate_per_minute)
    processed = existing = failed = 0
    try:
        async with engine.connect() as conn:
            query = (
                sa.select(
                    procurement_acts.c.id,
                    procurement_acts.c.title,
                    source_records.c.resource_type,
                    act_identifiers.c.value_normalized.label("adam"),
                )
                .select_from(
                    procurement_acts
                    .join(source_records, source_records.c.id == procurement_acts.c.source_record_id)
                    .join(
                        act_identifiers,
                        sa.and_(
                            act_identifiers.c.act_id == procurement_acts.c.id,
                            act_identifiers.c.scheme == "ADAM",
                        ),
                    )
                )
                .where(
                    procurement_acts.c.is_current.is_(True),
                    source_records.c.source_system == "KHMDHS",
                    source_records.c.resource_type.in_(ALL_RESOURCES),
                    ~sa.exists(
                        sa.select(documents.c.id).where(
                            documents.c.act_id == procurement_acts.c.id,
                            documents.c.document_type
                            == sa.func.concat(
                                "KHMDHS_",
                                sa.func.upper(source_records.c.resource_type),
                                "_PDF",
                            ),
                        )
                    ),
                )
                .order_by(
                    sa.case((source_records.c.resource_type == "notice", 0), else_=1),
                    procurement_acts.c.publication_date.desc().nulls_last(),
                )
                .limit(args.limit)
            )
            if args.resource:
                query = query.where(source_records.c.resource_type == args.resource)
            if args.adam:
                query = query.where(
                    act_identifiers.c.value_normalized == args.adam.strip().upper()
                )
            rows = (await conn.execute(query)).all()

            for row in rows:
                try:
                    await rate_limiter.acquire()
                    result = await process_khmdhs_attachment(
                        conn,
                        resource=row.resource_type,
                        adam=row.adam,
                        act_id=row.id,
                        title=row.title,
                    )
                    if result is None:
                        existing += 1
                    else:
                        processed += 1
                    await conn.commit()
                    print(
                        f"{processed + existing + failed}/{len(rows)} "
                        f"{row.adam} {khmdhs_document_type(row.resource_type)}"
                    )
                except Exception as exc:  # noqa: BLE001 - continue mode isolates provider/document failures
                    await conn.rollback()
                    failed += 1
                    print(f"failed {row.adam}: {type(exc).__name__}: {exc}")
                    if not args.continue_on_error:
                        raise
    finally:
        await engine.dispose()
    print(f"document backfill complete: processed={processed} existing={existing} failed={failed}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--resource", choices=sorted(ALL_RESOURCES))
    parser.add_argument("--adam")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("DOCUMENT_BACKFILL_LIMIT", "100")))
    parser.add_argument("--rate-per-minute", type=float, default=30)
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
