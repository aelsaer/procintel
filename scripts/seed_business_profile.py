#!/usr/bin/env python3
"""Seed a first business profile as tenant-scoped alert/search data.

Example:
    DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel \
    python scripts/seed_business_profile.py \
      --tenant-name "Demo ICT Supplier" \
      --email sales@example.test \
      --cpv-prefix 72 --cpv-prefix 488 \
      --nuts-code EL3 \
      --keyword λογισμικό \
      --amount-min 10000 \
      --score-now
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.domain.tables import alert_rules, saved_searches, tenant_memberships, tenants, users  # noqa: E402
from services.analytics.opportunity_scoring import score_opportunities_for_tenant  # noqa: E402


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def _build_filters(args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if args.cpv_prefix:
        filters["cpv_prefixes"] = args.cpv_prefix
    if args.nuts_code:
        filters["nuts_codes"] = [code.upper() for code in args.nuts_code]
    if args.keyword:
        filters["keywords"] = args.keyword
    if args.amount_min is not None:
        filters["amount_min"] = args.amount_min
    if args.amount_max is not None:
        filters["amount_max"] = args.amount_max
    return filters


async def _find_or_create_tenant(conn, name: str) -> uuid.UUID:
    row = (await conn.execute(select(tenants.c.id).where(tenants.c.name == name))).first()
    if row is not None:
        return row.id
    tenant_id = uuid.uuid4()
    await conn.execute(tenants.insert().values(id=tenant_id, name=name))
    return tenant_id


async def _upsert_user(conn, *, email: str, display_name: str | None) -> uuid.UUID:
    user_id = uuid.uuid4()
    stmt = (
        pg_insert(users)
        .values(id=user_id, email=email, display_name=display_name)
        .on_conflict_do_update(
            index_elements=["email"],
            set_={"display_name": display_name},
        )
        .returning(users.c.id)
    )
    return (await conn.execute(stmt)).scalar_one()


async def _upsert_rule(
    conn,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    filters: dict[str, Any],
    delivery_channels: list[str],
) -> uuid.UUID:
    row = (
        await conn.execute(
            select(alert_rules.c.id).where(alert_rules.c.tenant_id == tenant_id, alert_rules.c.name == name)
        )
    ).first()
    values = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "name": name,
        "event_types": ["opportunity.created", "opportunity.updated", "contract.expiring"],
        "filters": filters,
        "schedule": "IMMEDIATE",
        "delivery_channels": delivery_channels,
        "is_active": True,
    }
    if row is not None:
        await conn.execute(alert_rules.update().where(alert_rules.c.id == row.id).values(**values))
        return row.id
    rule_id = uuid.uuid4()
    await conn.execute(alert_rules.insert().values(id=rule_id, **values))
    return rule_id


async def _upsert_saved_search(
    conn,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    query: dict[str, Any],
) -> uuid.UUID:
    row = (
        await conn.execute(
            select(saved_searches.c.id).where(
                saved_searches.c.tenant_id == tenant_id,
                saved_searches.c.user_id == user_id,
                saved_searches.c.name == name,
            )
        )
    ).first()
    if row is not None:
        await conn.execute(saved_searches.update().where(saved_searches.c.id == row.id).values(query=query))
        return row.id
    saved_search_id = uuid.uuid4()
    await conn.execute(
        saved_searches.insert().values(
            id=saved_search_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            query=query,
        )
    )
    return saved_search_id


async def _run(args: argparse.Namespace) -> None:
    filters = _build_filters(args)
    if not filters:
        raise SystemExit("Add at least one --cpv-prefix, --nuts-code, --keyword, or amount filter.")

    engine = create_async_engine(_to_asyncpg_url(args.database_url))
    try:
        async with engine.connect() as conn:
            tenant_id = await _find_or_create_tenant(conn, args.tenant_name)
            user_id = await _upsert_user(conn, email=args.email, display_name=args.display_name)
            await conn.execute(
                pg_insert(tenant_memberships)
                .values(id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, role=args.role)
                .on_conflict_do_nothing(index_elements=["tenant_id", "user_id"])
            )
            rule_id = await _upsert_rule(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                name=args.rule_name,
                filters=filters,
                delivery_channels=args.delivery_channel,
            )
            saved_search_id = await _upsert_saved_search(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                name=args.saved_search_name,
                query=filters,
            )
            await conn.commit()

            print(f"tenant_id={tenant_id}")
            print(f"user_id={user_id}")
            print(f"alert_rule_id={rule_id}")
            print(f"saved_search_id={saved_search_id}")
            print(f"filters={filters}")

            if args.score_now:
                result = await score_opportunities_for_tenant(
                    conn,
                    tenant_id=tenant_id,
                    lookback_days=args.lookback_days,
                    include_contracted=args.include_contracted,
                    limit=args.score_limit,
                )
                print(
                    f"opportunity_scores: rules={result.rules_considered} "
                    f"candidates={result.candidates_seen} written={result.scores_written}"
                )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--role", default="OWNER")
    parser.add_argument("--rule-name", default="Business profile opportunities")
    parser.add_argument("--saved-search-name", default="Business profile search")
    parser.add_argument("--cpv-prefix", action="append", default=[])
    parser.add_argument("--nuts-code", action="append", default=[])
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--amount-min", type=float, default=None)
    parser.add_argument("--amount-max", type=float, default=None)
    parser.add_argument("--delivery-channel", action="append", default=["IN_APP"])
    parser.add_argument("--score-now", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--include-contracted", action="store_true")
    parser.add_argument("--score-limit", type=int, default=None)
    args = parser.parse_args()

    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
