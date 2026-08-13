"""Deliver due bid reminders through in-app state or configured webhooks."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    bid_reminders,
    bid_requirements,
    bid_tasks,
    bid_workspaces,
    users,
)
from packages.tenancy import all_tenant_ids, tenant_session

MAX_ATTEMPTS = 8
LEASE_TIMEOUT = timedelta(minutes=15)
RETRY_BASE_SECONDS = 60
RETRY_CAP_SECONDS = 6 * 60 * 60


def _next_retry_at(now: datetime, attempt_count: int) -> datetime:
    delay = min(
        RETRY_BASE_SECONDS * (2 ** max(attempt_count - 1, 0)),
        RETRY_CAP_SECONDS,
    )
    return now + timedelta(seconds=delay)


async def deliver_due_reminders(
    conn: AsyncConnection,
    http_client: httpx.AsyncClient,
    *,
    limit: int = 500,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    counts = {"processed": 0, "sent": 0, "failed": 0}
    for tenant_id in await all_tenant_ids(conn):
        async with tenant_session(conn, tenant_id):
            await conn.execute(
                bid_reminders.update()
                .where(
                    bid_reminders.c.tenant_id == tenant_id,
                    bid_reminders.c.status == "DELIVERING",
                    sa.or_(
                        bid_reminders.c.last_attempt_at.is_(None),
                        bid_reminders.c.last_attempt_at < now - LEASE_TIMEOUT,
                    ),
                )
                .values(
                    status="PENDING",
                    next_retry_at=now,
                    last_error={"message": "Recovered stale delivery lease"},
                )
            )
            candidate_ids = (
                sa.select(bid_reminders.c.id)
                .where(
                    bid_reminders.c.tenant_id == tenant_id,
                    bid_reminders.c.status == "PENDING",
                    bid_reminders.c.remind_at <= now,
                    sa.or_(
                        bid_reminders.c.next_retry_at.is_(None),
                        bid_reminders.c.next_retry_at <= now,
                    ),
                    bid_reminders.c.attempt_count < MAX_ATTEMPTS,
                )
                .order_by(bid_reminders.c.remind_at)
                .limit(max(0, limit - counts["processed"]))
                .with_for_update(skip_locked=True)
            )
            claimed = (
                await conn.execute(
                    bid_reminders.update()
                    .where(bid_reminders.c.id.in_(candidate_ids))
                    .values(
                        status="DELIVERING",
                        attempt_count=bid_reminders.c.attempt_count + 1,
                        last_attempt_at=now,
                        next_retry_at=None,
                        last_error=None,
                    )
                    .returning(bid_reminders.c.id)
                )
            ).scalars().all()
            await conn.commit()
            if not claimed:
                continue
            rows = (
                await conn.execute(
                    sa.select(
                        bid_reminders,
                        bid_workspaces.c.process_id,
                        bid_tasks.c.title.label("task_title"),
                        bid_requirements.c.title.label("requirement_title"),
                        users.c.email.label("recipient_email"),
                    )
                    .join(
                        bid_workspaces,
                        bid_workspaces.c.id == bid_reminders.c.bid_workspace_id,
                    )
                    .outerjoin(bid_tasks, bid_tasks.c.id == bid_reminders.c.task_id)
                    .outerjoin(
                        bid_requirements,
                        bid_requirements.c.id == bid_reminders.c.requirement_id,
                    )
                    .outerjoin(users, users.c.id == bid_reminders.c.assigned_user_id)
                    .where(bid_reminders.c.id.in_(claimed))
                )
            ).mappings().all()
            for row in rows:
                counts["processed"] += 1
                channel = row["channel"]
                delivered = channel == "IN_APP"
                terminal = False
                error_message: str | None = None
                if channel != "IN_APP":
                    url = os.environ.get(
                        "BID_REMINDER_EMAIL_WEBHOOK_URL"
                        if channel == "EMAIL"
                        else "BID_REMINDER_WEBHOOK_URL"
                    )
                    if not url:
                        terminal = True
                        error_message = f"{channel} reminder delivery is not configured"
                    else:
                        try:
                            async with http_client.stream(
                                "POST",
                                url,
                                json={
                                    "reminder_id": str(row["id"]),
                                    "tenant_id": str(row["tenant_id"]),
                                    "process_id": str(row["process_id"]),
                                    "recipient": row["recipient_email"],
                                    "task": row["task_title"],
                                    "requirement": row["requirement_title"],
                                    "remind_at": row["remind_at"].isoformat(),
                                },
                                follow_redirects=False,
                            ) as response:
                                delivered = 200 <= response.status_code < 300
                                if not delivered:
                                    error_message = f"Reminder endpoint returned {response.status_code}"
                        except httpx.HTTPError as exc:
                            error_message = str(exc)

                if delivered:
                    values = {
                        "status": "SENT",
                        "sent_at": now,
                        "next_retry_at": None,
                        "last_error": None,
                    }
                    counts["sent"] += 1
                else:
                    terminal = terminal or row["attempt_count"] >= MAX_ATTEMPTS
                    values = {
                        "status": "FAILED" if terminal else "PENDING",
                        "sent_at": None,
                        "next_retry_at": (
                            None
                            if terminal
                            else _next_retry_at(now, row["attempt_count"])
                        ),
                        "last_error": {"message": error_message or "Reminder delivery failed"},
                    }
                    counts["failed"] += 1
                await conn.execute(
                    bid_reminders.update()
                    .where(
                        bid_reminders.c.id == row["id"],
                        bid_reminders.c.status == "DELIVERING",
                        bid_reminders.c.last_attempt_at == now,
                    )
                    .values(**values)
                )
                await conn.commit()
        if counts["processed"] >= limit:
            break
    return counts
