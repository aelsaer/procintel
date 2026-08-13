"""Scheduled analytics-mart refresh (spec §37's dependency chain, applied
to `db/marts/analytics_marts.sql`'s materialized views).

The 10 materialized views (`opportunity_scores` is a plain table populated
by application code and is deliberately untouched here) are
refreshed with plain `REFRESH MATERIALIZED VIEW`, not `CONCURRENTLY`:
`CONCURRENTLY` requires a unique index on the view, and only
`market_value_metrics` has one today. Adding unique indexes to the other
remaining views so they can refresh without blocking concurrent reads is a
legitimate future optimization, not attempted here.

Refresh order matters: `market_hhi` reads from `supplier_market_share` +
`market_value_metrics`, and `renewal_signals` reads from
`cycle_time_metrics` — both refreshed strictly after their dependencies
below. A dependent view is skipped when one of its inputs failed in the
same pass, which avoids recording stale derived metrics as freshly rebuilt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import mart_refresh_state
from packages.source_clients.pg_lock import advisory_unlock, try_advisory_lock

MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER: tuple[str, ...] = (
    "market_value_metrics",
    "supplier_market_share",
    "market_hhi",  # depends on the two above
    "buyer_concentration",
    "supplier_dependency",
    "incumbent_signals",
    "contract_modification_stats",
    "cycle_time_metrics",
    "payment_execution",
    "renewal_signals",  # depends on cycle_time_metrics
)

_LOCK_KEY = "procintel:analytics:mart_refresh"

MART_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "market_hhi": ("market_value_metrics", "supplier_market_share"),
    "renewal_signals": ("cycle_time_metrics",),
}


@dataclass(frozen=True)
class MartRefreshOutcome:
    mart_name: str
    succeeded: bool
    error: str | None = None


async def _ensure_state_row(conn: AsyncConnection, mart_name: str) -> None:
    await conn.execute(
        sa.text(
            "INSERT INTO mart_refresh_state (mart_name) VALUES (:mart_name) "
            "ON CONFLICT (mart_name) DO NOTHING"
        ),
        {"mart_name": mart_name},
    )


async def refresh_mart(conn: AsyncConnection, mart_name: str) -> None:
    if mart_name not in MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER:
        raise ValueError(f"unknown mart: {mart_name!r}")
    # Identifier can't be bound as a parameter; safe here because mart_name
    # is checked against the fixed whitelist above, never caller-supplied
    # free text.
    await conn.execute(sa.text(f"REFRESH MATERIALIZED VIEW {mart_name}"))


async def refresh_all_marts(conn: AsyncConnection, *, now: datetime | None = None) -> list[MartRefreshOutcome]:
    """Refreshes every materialized view in dependency order under one
    advisory lock (a single global lock, not one per mart — the whole
    point is a strictly ordered pass, so running two overlapping passes
    concurrently is never useful). Returns one outcome per mart; a failure
    on one mart doesn't stop independent marts from being attempted."""
    now = now or datetime.now(timezone.utc)
    if not await try_advisory_lock(conn, _LOCK_KEY):
        return []

    outcomes: list[MartRefreshOutcome] = []
    failed_marts: set[str] = set()
    try:
        for mart_name in MATERIALIZED_VIEWS_IN_DEPENDENCY_ORDER:
            await _ensure_state_row(conn, mart_name)
            await conn.execute(
                mart_refresh_state.update()
                .where(mart_refresh_state.c.mart_name == mart_name)
                .values(last_refresh_started_at=now)
            )
            await conn.commit()

            failed_dependencies = [
                dependency
                for dependency in MART_DEPENDENCIES.get(mart_name, ())
                if dependency in failed_marts
            ]
            if failed_dependencies:
                message = f"skipped because dependencies failed: {', '.join(failed_dependencies)}"
                await conn.execute(
                    mart_refresh_state.update()
                    .where(mart_refresh_state.c.mart_name == mart_name)
                    .values(
                        last_error={
                            "type": "DependencyRefreshFailed",
                            "message": message,
                            "dependencies": failed_dependencies,
                        }
                    )
                )
                await conn.commit()
                failed_marts.add(mart_name)
                outcomes.append(MartRefreshOutcome(mart_name=mart_name, succeeded=False, error=message))
                continue

            try:
                await refresh_mart(conn, mart_name)
            except Exception as exc:  # noqa: BLE001 — recorded per-mart, doesn't stop the rest
                # A failed REFRESH leaves PostgreSQL's transaction aborted.
                # Roll it back before writing durable failure state.
                await conn.rollback()
                await conn.execute(
                    mart_refresh_state.update()
                    .where(mart_refresh_state.c.mart_name == mart_name)
                    .values(last_error={"type": type(exc).__name__, "message": str(exc)})
                )
                await conn.commit()
                failed_marts.add(mart_name)
                outcomes.append(MartRefreshOutcome(mart_name=mart_name, succeeded=False, error=str(exc)))
                continue

            await conn.execute(
                mart_refresh_state.update()
                .where(mart_refresh_state.c.mart_name == mart_name)
                .values(last_refresh_finished_at=datetime.now(timezone.utc), last_error=None)
            )
            await conn.commit()
            outcomes.append(MartRefreshOutcome(mart_name=mart_name, succeeded=True))
    finally:
        if conn.in_transaction():
            await conn.rollback()
        await advisory_unlock(conn, _LOCK_KEY)
        await conn.commit()

    return outcomes
