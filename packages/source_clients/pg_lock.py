"""Postgres advisory-lock helpers — mutual exclusion between concurrent
scheduler processes, without a new lock service (Redis/etcd/...). Used by
`services/ingestion/orchestration/scheduler.py` and
`services/analytics/refresh.py` so two schedulers never run the same
partition/mart-refresh pass at once.

Session-level (`pg_advisory_lock`/`pg_advisory_unlock`), not transaction-
level (`pg_advisory_xact_lock`) — callers hold the lock across several
separate commits (e.g. one for `last_attempt_at`, one for the final
result), so it must survive commits and be released explicitly.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection


async def try_advisory_lock(conn: AsyncConnection, key: str) -> bool:
    return bool((await conn.execute(sa.text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key})).scalar())


async def advisory_unlock(conn: AsyncConnection, key: str) -> None:
    await conn.execute(sa.text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
