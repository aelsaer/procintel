"""Atomic, shared fixed-window request throttling backed by PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after: int


async def consume_request_quota(
    conn: AsyncConnection,
    identities: tuple[str, ...],
    *,
    limit: int,
) -> RateLimitDecision:
    """Increment all identities atomically and return the strictest result."""
    if limit < 1:
        raise ValueError("limit must be positive")
    unique = tuple(dict.fromkeys(identity for identity in identities if identity))
    if not unique:
        raise ValueError("at least one identity is required")
    rows = (
        await conn.execute(
            sa.text(
                """
                INSERT INTO api_rate_limit_windows (identity, window_start, request_count)
                SELECT identity, date_trunc('minute', clock_timestamp()), 1
                FROM unnest(CAST(:identities AS text[])) AS identities(identity)
                ON CONFLICT (identity, window_start) DO UPDATE
                SET request_count = api_rate_limit_windows.request_count + 1
                RETURNING request_count,
                          GREATEST(1, CEIL(EXTRACT(EPOCH FROM
                              (window_start + INTERVAL '1 minute' - clock_timestamp())
                          )))::integer AS retry_after
                """
            ),
            {"identities": list(unique)},
        )
    ).all()
    highest_count = max(int(row.request_count) for row in rows)
    retry_after = max(int(row.retry_after) for row in rows)
    return RateLimitDecision(
        allowed=highest_count <= limit,
        remaining=max(0, limit - highest_count),
        retry_after=retry_after,
    )


async def prune_request_quota(conn: AsyncConnection) -> None:
    await conn.execute(
        sa.text(
            "DELETE FROM api_rate_limit_windows "
            "WHERE window_start < clock_timestamp() - INTERVAL '5 minutes'"
        )
    )
