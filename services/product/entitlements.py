"""Commercial entitlement evaluation, usage accounting and Stripe signatures."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection


class EntitlementLimitExceeded(Exception):
    def __init__(self, metric_code: str, limit: int, usage: int) -> None:
        super().__init__(f"{metric_code} limit reached ({usage}/{limit})")
        self.metric_code = metric_code
        self.limit = limit
        self.usage = usage


def effective_entitlements(
    plan_entitlements: dict[str, Any],
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    return {**plan_entitlements, **(override or {})}


def monthly_period(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    if day.month == 12:
        end = day.replace(year=day.year + 1, month=1, day=1)
    else:
        end = day.replace(month=day.month + 1, day=1)
    return start, end


def usage_permitted(limit: int | bool | None, current: int, increment: int = 1) -> bool:
    if isinstance(limit, bool):
        return limit
    if limit is None:
        return False
    if int(limit) < 0:
        return True
    return current + increment <= int(limit)


def verify_stripe_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
    *,
    now: int | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    values: dict[str, list[str]] = {}
    for item in signature_header.split(","):
        key, _, value = item.partition("=")
        values.setdefault(key.strip(), []).append(value.strip())
    try:
        timestamp = int(values["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > tolerance_seconds:
        return False
    signed = str(timestamp).encode("ascii") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in values.get("v1", []))


async def consume_entitlement(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    metric_code: str,
    increment: int = 1,
) -> tuple[int, int]:
    from packages.domain.tables import entitlement_usage, saas_plans, tenant_subscriptions

    subscription = (
        await conn.execute(
            sa.select(
                tenant_subscriptions.c.entitlements_override,
                saas_plans.c.entitlements,
            )
            .join(saas_plans, saas_plans.c.code == tenant_subscriptions.c.plan_code)
            .where(
                tenant_subscriptions.c.tenant_id == tenant_id,
                tenant_subscriptions.c.status.in_(("TRIALING", "ACTIVE")),
            )
        )
    ).first()
    if subscription is None:
        raise EntitlementLimitExceeded(metric_code, 0, 0)
    entitlements = effective_entitlements(
        dict(subscription.entitlements or {}),
        dict(subscription.entitlements_override or {}),
    )
    raw_limit = entitlements.get(metric_code)
    if isinstance(raw_limit, bool) or raw_limit is None:
        if not usage_permitted(raw_limit, 0, increment):
            raise EntitlementLimitExceeded(metric_code, int(bool(raw_limit)), 0)
        return int(bool(raw_limit)), 0
    limit = int(raw_limit)
    period_start, period_end = monthly_period(datetime.now(timezone.utc).date())
    statement = pg_insert(entitlement_usage).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_code=metric_code,
        period_start=period_start,
        period_end=period_end,
        usage_count=0,
    )
    await conn.execute(
        statement.on_conflict_do_nothing(
            index_elements=[
                entitlement_usage.c.tenant_id,
                entitlement_usage.c.metric_code,
                entitlement_usage.c.period_start,
                entitlement_usage.c.period_end,
            ]
        )
    )
    current = (
        await conn.execute(
            sa.select(entitlement_usage.c.usage_count)
            .where(
                entitlement_usage.c.tenant_id == tenant_id,
                entitlement_usage.c.metric_code == metric_code,
                entitlement_usage.c.period_start == period_start,
                entitlement_usage.c.period_end == period_end,
            )
            .with_for_update()
        )
    ).scalar_one()
    if not usage_permitted(limit, int(current), increment):
        raise EntitlementLimitExceeded(metric_code, limit, int(current))
    await conn.execute(
        entitlement_usage.update()
        .where(
            entitlement_usage.c.tenant_id == tenant_id,
            entitlement_usage.c.metric_code == metric_code,
            entitlement_usage.c.period_start == period_start,
            entitlement_usage.c.period_end == period_end,
        )
        .values(
            usage_count=entitlement_usage.c.usage_count + increment,
            last_incremented_at=datetime.now(timezone.utc),
        )
    )
    return limit, int(current) + increment
