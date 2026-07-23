#!/usr/bin/env python3
"""Backfill summary-ready ΚΗΜΔΗΣ fields from already stored raw payloads."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.domain.tables import act_identifiers, procurement_acts, source_records  # noqa: E402
from services.ingestion.connectors.khmdhs.client import ALL_RESOURCES  # noqa: E402
from services.ingestion.connectors.khmdhs.normalize import normalize_khmdhs_record  # noqa: E402


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def _payload_path(payload_uri: str, raw_root: Path) -> Path | None:
    if "://" in payload_uri and not payload_uri.startswith("file://"):
        return None
    path = Path(payload_uri.removeprefix("file://"))
    if path.exists():
        return path
    candidates = (PROJECT_ROOT / path, raw_root / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


async def _run(args: argparse.Namespace) -> None:
    engine = create_async_engine(_to_asyncpg_url(args.database_url))
    updated = skipped = failed = 0
    try:
        async with engine.connect() as conn:
            query = (
                sa.select(
                    procurement_acts.c.id,
                    source_records.c.resource_type,
                    source_records.c.payload_uri,
                )
                .select_from(
                    procurement_acts.join(
                        source_records,
                        source_records.c.id == procurement_acts.c.source_record_id,
                    )
                )
                .where(
                    source_records.c.source_system == "KHMDHS",
                    source_records.c.resource_type.in_(ALL_RESOURCES),
                )
                .order_by(procurement_acts.c.id)
            )
            if not args.overwrite:
                query = query.where(procurement_acts.c.source_details == sa.text("'{}'::jsonb"))
            if args.resource:
                query = query.where(source_records.c.resource_type == args.resource)
            if args.adam:
                query = query.where(
                    sa.exists(
                        sa.select(act_identifiers.c.id).where(
                            act_identifiers.c.act_id == procurement_acts.c.id,
                            act_identifiers.c.scheme == "ADAM",
                            act_identifiers.c.value_normalized == args.adam.strip().upper(),
                        )
                    )
                )
            if args.limit:
                query = query.limit(args.limit)

            rows = (await conn.execute(query)).all()
            for row in rows:
                try:
                    path = _payload_path(row.payload_uri, Path(args.raw_root))
                    if path is None:
                        skipped += 1
                        continue
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    normalized = normalize_khmdhs_record(payload, resource=row.resource_type)
                    await conn.execute(
                        procurement_acts.update()
                        .where(procurement_acts.c.id == row.id)
                        .values(
                            publication_date=normalized.publication_date,
                            submission_deadline=normalized.submission_deadline,
                            procedure_type=normalized.procedure_type,
                            source_details=normalized.source_details,
                        )
                    )
                    updated += 1
                    if updated % args.commit_every == 0:
                        await conn.commit()
                        print(f"updated={updated} skipped={skipped} failed={failed}")
                except Exception as exc:  # noqa: BLE001 - one malformed historical payload is isolated
                    failed += 1
                    print(f"failed act={row.id} payload={row.payload_uri}: {type(exc).__name__}: {exc}")
                    if not args.continue_on_error:
                        raise
            await conn.commit()
    finally:
        await engine.dispose()
    print(
        f"publication details complete: updated={updated} "
        f"skipped={skipped} failed={failed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--raw-root", default=os.environ.get("RAW_STORE_ROOT", "./raw"))
    parser.add_argument("--resource", choices=sorted(ALL_RESOURCES))
    parser.add_argument("--adam")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--commit-every", type=int, default=500)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
