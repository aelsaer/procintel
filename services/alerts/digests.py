"""Timezone-aware daily and weekly alert digest processing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import alert_digest_runs, alert_events, alert_rules

from .delivery import DeliveryChannel

DIGEST_LEASE_TIMEOUT = timedelta(minutes=15)
DIGEST_RETRY_BASE_SECONDS = 5 * 60
DIGEST_RETRY_CAP_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class DigestSweepResult:
    rules_checked: int
    digests_created: int
    events_included: int


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Athens")


def _period_key(schedule: str, local_now: datetime) -> str:
    if schedule == "WEEKLY_DIGEST":
        year, week, _ = local_now.isocalendar()
        return f"{year}-W{week:02d}"
    return local_now.date().isoformat()


def _is_due(schedule: str, local_now: datetime, digest_time: object) -> bool:
    if schedule not in {"DAILY_DIGEST", "WEEKLY_DIGEST"}:
        return False
    if local_now.time().replace(tzinfo=None) < digest_time:
        return False
    return schedule != "WEEKLY_DIGEST" or local_now.isoweekday() == 1


def _retry_at(now: datetime, attempt_count: int) -> datetime:
    delay = min(
        DIGEST_RETRY_BASE_SECONDS * (2 ** max(attempt_count - 1, 0)),
        DIGEST_RETRY_CAP_SECONDS,
    )
    return now + timedelta(seconds=delay)


async def _claim_digest_run(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    alert_rule_id: uuid.UUID,
    schedule: str,
    period_key: str,
    period_start: datetime,
    period_end: datetime,
    event_count: int,
    channels: list[str],
    now: datetime,
) -> tuple[uuid.UUID, int] | None:
    """Atomically create or lease one digest period for delivery."""
    inserted = (
        await conn.execute(
            pg_insert(alert_digest_runs)
            .values(
                id=run_id,
                tenant_id=tenant_id,
                alert_rule_id=alert_rule_id,
                schedule=schedule,
                period_key=period_key,
                period_started_at=period_start,
                period_ended_at=period_end,
                event_count=event_count,
                status="RUNNING",
                channels=channels,
                error=None,
                attempt_count=1,
                last_attempt_at=now,
                next_retry_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=["alert_rule_id", "schedule", "period_key"],
                index_where=alert_digest_runs.c.alert_rule_id.is_not(None),
            )
            .returning(
                alert_digest_runs.c.id,
                alert_digest_runs.c.attempt_count,
            )
        )
    ).first()
    if inserted is not None:
        await conn.commit()
        return inserted.id, int(inserted.attempt_count)

    reclaimed = (
        await conn.execute(
            alert_digest_runs.update()
            .where(
                alert_digest_runs.c.alert_rule_id == alert_rule_id,
                alert_digest_runs.c.schedule == schedule,
                alert_digest_runs.c.period_key == period_key,
                sa.or_(
                    alert_digest_runs.c.status == "PENDING",
                    sa.and_(
                        alert_digest_runs.c.status == "FAILED",
                        sa.or_(
                            alert_digest_runs.c.next_retry_at.is_(None),
                            alert_digest_runs.c.next_retry_at <= now,
                        ),
                    ),
                    sa.and_(
                        alert_digest_runs.c.status == "RUNNING",
                        sa.or_(
                            alert_digest_runs.c.last_attempt_at.is_(None),
                            alert_digest_runs.c.last_attempt_at < now - DIGEST_LEASE_TIMEOUT,
                        ),
                    ),
                ),
            )
            .values(
                status="RUNNING",
                period_started_at=period_start,
                period_ended_at=period_end,
                event_count=event_count,
                channels=channels,
                error=None,
                attempt_count=alert_digest_runs.c.attempt_count + 1,
                last_attempt_at=now,
                next_retry_at=None,
            )
            .returning(
                alert_digest_runs.c.id,
                alert_digest_runs.c.attempt_count,
            )
        )
    ).first()
    await conn.commit()
    return (reclaimed.id, int(reclaimed.attempt_count)) if reclaimed is not None else None


async def process_due_digests(
    conn: AsyncConnection,
    *,
    delivery_channel: DeliveryChannel,
    now: datetime | None = None,
) -> DigestSweepResult:
    """Create and deliver each due digest exactly once per local period."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    rules = (
        await conn.execute(
            sa.select(alert_rules).where(
                alert_rules.c.is_active.is_(True),
                alert_rules.c.schedule.in_(("DAILY_DIGEST", "WEEKLY_DIGEST")),
            )
        )
    ).all()
    digests_created = 0
    events_included = 0

    for rule in rules:
        local_now = now.astimezone(_timezone(rule.timezone))
        if not _is_due(rule.schedule, local_now, rule.digest_time):
            continue
        period_key = _period_key(rule.schedule, local_now)
        last_period_end = (
            await conn.execute(
                sa.select(sa.func.max(alert_digest_runs.c.period_ended_at)).where(
                    alert_digest_runs.c.alert_rule_id == rule.id,
                    alert_digest_runs.c.status.in_(("DELIVERED", "PARTIAL")),
                )
            )
        ).scalar_one()
        period_start = last_period_end or rule.created_at
        events = (
            await conn.execute(
                sa.select(alert_events)
                .where(
                    alert_events.c.alert_rule_id == rule.id,
                    alert_events.c.triggered_at > period_start,
                    alert_events.c.triggered_at <= now,
                )
                .order_by(alert_events.c.triggered_at)
            )
        ).all()
        run_id = uuid.uuid4()
        channels = list(rule.delivery_channels or [])
        claim = await _claim_digest_run(
            conn,
            run_id=run_id,
            tenant_id=rule.tenant_id,
            alert_rule_id=rule.id,
            schedule=rule.schedule,
            period_key=period_key,
            period_start=period_start,
            period_end=now,
            event_count=len(events),
            channels=channels,
            now=now,
        )
        if claim is None:
            continue
        run_id, attempt_count = claim

        try:
            if events:
                latest = events[-1]
                await delivery_channel.deliver(
                    conn,
                    alert_rule_id=rule.id,
                    tenant_id=rule.tenant_id,
                    alert_event_id=latest.id,
                    event_type="alert.digest",
                    payload={
                        "rule_name": rule.name,
                        "schedule": rule.schedule,
                        "period_started_at": period_start.isoformat(),
                        "period_ended_at": now.isoformat(),
                        "event_count": len(events),
                        "events": [
                            {
                                "event_type": event.event_type,
                                "object_id": str(event.canonical_object_id),
                                "triggered_at": event.triggered_at.isoformat(),
                                "title": (event.payload or {}).get("title"),
                            }
                            for event in events[-20:]
                        ],
                    },
                )
            await conn.execute(
                alert_digest_runs.update()
                .where(
                    alert_digest_runs.c.id == run_id,
                    alert_digest_runs.c.status == "RUNNING",
                )
                .values(
                    status="DELIVERED",
                    delivered_at=now,
                    next_retry_at=None,
                    error=None,
                )
            )
        except Exception as exc:
            await conn.rollback()
            await conn.execute(
                alert_digest_runs.update()
                .where(
                    alert_digest_runs.c.id == run_id,
                    alert_digest_runs.c.status == "RUNNING",
                )
                .values(
                    status="FAILED",
                    next_retry_at=_retry_at(now, attempt_count),
                    error={"message": str(exc)},
                )
            )
        await conn.commit()
        digests_created += 1
        events_included += len(events)

    return DigestSweepResult(
        rules_checked=len(rules),
        digests_created=digests_created,
        events_included=events_included,
    )
