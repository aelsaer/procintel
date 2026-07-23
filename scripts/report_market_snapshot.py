#!/usr/bin/env python3
"""Print a compact procurement-data snapshot from the local database."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _print_rows(conn, title: str, sql: str, params: dict) -> None:
    print(f"\n{title}")
    rows = (await conn.execute(sa.text(sql), params)).all()
    if not rows:
        print("  no rows")
        return
    for row in rows:
        print("  " + " | ".join(f"{key}={value}" for key, value in row._mapping.items()))


async def _run(args: argparse.Namespace) -> None:
    engine = create_async_engine(_to_asyncpg_url(args.database_url))
    params = {"date_from": args.date_from, "date_to": args.date_to, "limit": args.limit}
    try:
        async with engine.connect() as conn:
            await _print_rows(
                conn,
                "Act counts",
                """
                SELECT act_type, COUNT(*) AS count
                FROM procurement_acts
                WHERE COALESCE(publication_date, submission_date, decision_date)
                      BETWEEN :date_from AND :date_to
                GROUP BY act_type
                ORDER BY act_type
                """,
                params,
            )
            await _print_rows(
                conn,
                "Coverage",
                """
                SELECT
                    COUNT(DISTINCT a.process_id) AS processes,
                    COUNT(DISTINCT al.act_id) AS acts_with_geo,
                    COUNT(DISTINCT dd.id) AS diavgeia_decisions,
                    COUNT(DISTINCT ecs.entity_id) AS suppliers_with_gemi
                FROM procurement_acts a
                LEFT JOIN act_locations al ON al.act_id = a.id
                LEFT JOIN act_links dl ON dl.to_act_id = a.id AND dl.link_method IN ('EXACT_ADA', 'DIAVGEIA_SEARCH')
                LEFT JOIN procurement_acts dd ON dd.id = dl.from_act_id AND dd.act_type = 'DIAVGEIA_DECISION'
                LEFT JOIN act_parties sp ON sp.act_id = a.id AND sp.party_role IN ('SUPPLIER', 'CONTRACTOR')
                LEFT JOIN entity_company_snapshots ecs ON ecs.entity_id = sp.entity_id AND ecs.is_current = TRUE
                WHERE COALESCE(a.publication_date, a.submission_date, a.decision_date)
                      BETWEEN :date_from AND :date_to
                """,
                params,
            )
            await _print_rows(
                conn,
                "Top supplier AFMs by recorded contract value",
                """
                SELECT
                    ei.value_normalized AS afm,
                    e.canonical_name AS supplier,
                    SUM(ap.amount) AS value,
                    COUNT(DISTINCT a.id) AS contracts
                FROM procurement_acts a
                JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
                JOIN entities e ON e.id = ap.entity_id
                LEFT JOIN entity_identifiers ei
                    ON ei.entity_id = e.id AND ei.scheme = 'AFM' AND ei.is_current = TRUE
                WHERE a.act_type = 'CONTRACT'
                  AND COALESCE(a.decision_date, a.publication_date, a.submission_date)
                      BETWEEN :date_from AND :date_to
                GROUP BY ei.value_normalized, e.canonical_name
                ORDER BY SUM(ap.amount) DESC NULLS LAST
                LIMIT :limit
                """,
                params,
            )
            await _print_rows(
                conn,
                "Top CPV prefixes",
                """
                SELECT LEFT(acpv.cpv_code, 4) AS cpv_prefix, COUNT(DISTINCT a.id) AS acts, SUM(a.amount_gross) AS value
                FROM procurement_acts a
                JOIN act_cpv_codes acpv ON acpv.act_id = a.id
                WHERE COALESCE(a.publication_date, a.submission_date, a.decision_date)
                      BETWEEN :date_from AND :date_to
                GROUP BY LEFT(acpv.cpv_code, 4)
                ORDER BY COUNT(DISTINCT a.id) DESC, SUM(a.amount_gross) DESC NULLS LAST
                LIMIT :limit
                """,
                params,
            )
            await _print_rows(
                conn,
                "Top NUTS",
                """
                SELECT al.nuts_code, COUNT(DISTINCT a.id) AS acts, SUM(a.amount_gross) AS value
                FROM procurement_acts a
                JOIN act_locations al ON al.act_id = a.id
                WHERE COALESCE(a.publication_date, a.submission_date, a.decision_date)
                      BETWEEN :date_from AND :date_to
                GROUP BY al.nuts_code
                ORDER BY COUNT(DISTINCT a.id) DESC, SUM(a.amount_gross) DESC NULLS LAST
                LIMIT :limit
                """,
                params,
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--date-from", type=date.fromisoformat, default=date(2026, 6, 1))
    parser.add_argument("--date-to", type=date.fromisoformat, default=date(2026, 6, 30))
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
