"""`refresh_all_marts` against a real Postgres instance with the full
migrated schema (`db/migrations` + `db/marts`). Skipped automatically
unless $DATABASE_URL is set.

Doesn't seed procurement data — the materialized views are valid (if
empty) over an empty base-table set, so this only confirms the refresh
mechanics (ordering, `mart_refresh_state` bookkeeping, the advisory lock)
work against the real view definitions, not that the analytics formulas
themselves produce correct numbers (that's `db/marts/analytics_marts.sql`'s
own concern, not this refresh job's).
"""

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import mart_refresh_state
from services.analytics.refresh import MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER, refresh_all_marts

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


async def test_refresh_all_marts_succeeds_and_records_state_for_every_view():
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            outcomes = await refresh_all_marts(conn)
            assert len(outcomes) == len(MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER)
            assert all(o.succeeded for o in outcomes), [o for o in outcomes if not o.succeeded]

            rows = (await conn.execute(select(mart_refresh_state))).all()
            recorded_names = {row.mart_name for row in rows}
            assert recorded_names == set(MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER)
            for row in rows:
                assert row.last_refresh_finished_at is not None
                assert row.last_error is None
    finally:
        await engine.dispose()
