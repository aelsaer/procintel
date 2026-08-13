import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.request_rate_limit import consume_request_quota

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _asyncpg_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_shared_rate_limit_counts_across_connections():
    engine = create_async_engine(_asyncpg_url())
    identity = "integration:distributed-rate-limit"
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM api_rate_limit_windows WHERE identity = :identity"), {"identity": identity})
        async with engine.begin() as first:
            decision = await consume_request_quota(first, (identity,), limit=2)
            assert decision.allowed and decision.remaining == 1
        async with engine.begin() as second:
            decision = await consume_request_quota(second, (identity,), limit=2)
            assert decision.allowed and decision.remaining == 0
        async with engine.begin() as third:
            decision = await consume_request_quota(third, (identity,), limit=2)
            assert not decision.allowed and decision.retry_after >= 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM api_rate_limit_windows WHERE identity = :identity"), {"identity": identity})
        await engine.dispose()
