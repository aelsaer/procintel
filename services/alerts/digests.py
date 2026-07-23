"""Timezone-aware daily and weekly alert digest processing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import alert_digest_runs, alert_events, alert_rules

from .delivery import DeliveryChannel


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
        already_ran = (
            await conn.execute(
                sa.select(alert_digest_runs.c.id).where(
                    alert_digest_runs.c.alert_rule_id == rule.id,
                    alert_digest_runs.c.schedule == rule.schedule,
                    alert_digest_runs.c.period_key == period_key,
                )
            )
        ).first()
        if already_ran:
            continue

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
        await conn.execute(
            alert_digest_runs.insert().values(
                id=run_id,
                tenant_id=rule.tenant_id,
                alert_rule_id=rule.id,
                schedule=rule.schedule,
                period_key=period_key,
                period_started_at=period_start,
                period_ended_at=now,
                event_count=len(events),
                status="RUNNING",
                channels=channels,
                error=None,
            )
        )
        await conn.commit()

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
                .where(alert_digest_runs.c.id == run_id)
                .values(status="DELIVERED", delivered_at=now)
            )
        except Exception as exc:
            await conn.execute(
                alert_digest_runs.update()
                .where(alert_digest_runs.c.id == run_id)
                .values(status="FAILED", error={"message": str(exc)})
            )
        await conn.commit()
        digests_created += 1
        events_included += len(events)

    return DigestSweepResult(
        rules_checked=len(rules),
        digests_created=digests_created,
        events_included=events_included,
    )
