#!/usr/bin/env python3
"""Materialize missing ΚΗΜΔΗΣ CPV links from stored publication details."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.domain.tables import (  # noqa: E402
    act_cpv_codes,
    field_provenance,
    procurement_acts,
    source_records,
)

_CPV_PATTERN = re.compile(r"^\d{8}(?:-\d)?$")


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def _cpv_leaf(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("code", "key", "value"):
        if value.get(key):
            return value[key]
    return None


def extract_cpv_codes(source_details: Any) -> list[str]:
    """Return valid, ordered CPVs from normalized object details."""
    if not isinstance(source_details, dict):
        return []
    object_details = source_details.get("object_details")
    if not isinstance(object_details, list):
        return []

    result: list[str] = []
    for detail in object_details:
        if not isinstance(detail, dict):
            continue
        values = detail.get("cpv_codes")
        if not isinstance(values, (list, tuple)):
            values = [values] if values is not None else []
        for value in values:
            leaf = _cpv_leaf(value)
            code = re.sub(r"\s+", "", str(leaf or ""))
            if _CPV_PATTERN.fullmatch(code) and code not in result:
                result.append(code)
    return result


async def backfill_cpv_codes(
    database_url: str,
    *,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> dict[str, int]:
    engine = create_async_engine(_to_asyncpg_url(database_url))
    stats = {
        "acts_scanned": 0,
        "acts_repaired": 0,
        "codes_materialized": 0,
        "provenance_rows": 0,
        "without_valid_cpv": 0,
    }
    last_id = None
    try:
        async with engine.connect() as conn:
            while True:
                already_materialized = sa.exists(
                    sa.select(act_cpv_codes.c.act_id).where(
                        act_cpv_codes.c.act_id == procurement_acts.c.id
                    )
                )
                query = (
                    sa.select(
                        procurement_acts.c.id,
                        procurement_acts.c.source_record_id,
                        procurement_acts.c.source_details,
                        source_records.c.fetched_at,
                    )
                    .select_from(
                        procurement_acts.join(
                            source_records,
                            source_records.c.id == procurement_acts.c.source_record_id,
                        )
                    )
                    .where(
                        procurement_acts.c.is_current.is_(True),
                        source_records.c.source_system == "KHMDHS",
                        ~already_materialized,
                    )
                    .order_by(procurement_acts.c.id)
                    .limit(batch_size)
                )
                if last_id is not None:
                    query = query.where(procurement_acts.c.id > last_id)
                rows = (await conn.execute(query)).mappings().all()
                if not rows:
                    break
                last_id = rows[-1]["id"]
                stats["acts_scanned"] += len(rows)

                links: list[dict[str, Any]] = []
                provenance: list[dict[str, Any]] = []
                for row in rows:
                    codes = extract_cpv_codes(row["source_details"])
                    if not codes:
                        stats["without_valid_cpv"] += 1
                        continue
                    stats["acts_repaired"] += 1
                    links.extend(
                        {
                            "act_id": row["id"],
                            "cpv_code": code,
                            "is_primary": index == 0,
                            "source_record_id": row["source_record_id"],
                        }
                        for index, code in enumerate(codes)
                    )
                    provenance.append(
                        {
                            "id": uuid.uuid4(),
                            "object_type": "procurement_acts",
                            "object_id": row["id"],
                            "field_name": "cpv_codes",
                            "source_record_id": row["source_record_id"],
                            "source_path": "$.objectDetailsList[*].cpvs",
                            "extraction_method": "DIRECT_FIELD_MAPPING",
                            "confidence": 1,
                            "observed_at": row["fetched_at"],
                            "value_hash": hashlib.sha256(
                                "|".join(codes).encode("utf-8")
                            ).hexdigest(),
                        }
                    )

                stats["codes_materialized"] += len(links)
                stats["provenance_rows"] += len(provenance)
                if dry_run:
                    continue
                if links:
                    await conn.execute(
                        pg_insert(act_cpv_codes)
                        .values(links)
                        .on_conflict_do_nothing(
                            index_elements=[
                                act_cpv_codes.c.act_id,
                                act_cpv_codes.c.cpv_code,
                            ]
                        )
                    )
                if provenance:
                    await conn.execute(field_provenance.insert().values(provenance))
                await conn.commit()
    finally:
        await engine.dispose()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    print(
        json.dumps(
            asyncio.run(
                backfill_cpv_codes(
                    args.database_url,
                    batch_size=args.batch_size,
                    dry_run=args.dry_run,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
