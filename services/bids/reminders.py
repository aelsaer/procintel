"""Deliver due bid reminders through in-app state or configured webhooks."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    bid_reminders,
    bid_requirements,
    bid_tasks,
    bid_workspaces,
    tenants,
    users,
)


async def deliver_due_reminders(
    conn: AsyncConnection,
    http_client: httpx.AsyncClient,
    *,
    limit: int = 500,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    counts = {"processed": 0, "sent": 0, "failed": 0}
    tenant_ids = (await conn.execute(sa.select(tenants.c.id))).scalars().all()
    for tenant_id in tenant_ids:
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        rows = (
            await conn.execute(
                sa.select(
                    bid_reminders,
                    bid_workspaces.c.process_id,
                    bid_tasks.c.title.label("task_title"),
                    bid_requirements.c.title.label("requirement_title"),
                    users.c.email.label("recipient_email"),
                )
                .join(bid_workspaces, bid_workspaces.c.id == bid_reminders.c.bid_workspace_id)
                .outerjoin(bid_tasks, bid_tasks.c.id == bid_reminders.c.task_id)
                .outerjoin(bid_requirements, bid_requirements.c.id == bid_reminders.c.requirement_id)
                .outerjoin(users, users.c.id == bid_reminders.c.assigned_user_id)
                .where(
                    bid_reminders.c.tenant_id == tenant_id,
                    bid_reminders.c.status == "PENDING",
                    bid_reminders.c.remind_at <= now,
                )
                .order_by(bid_reminders.c.remind_at)
                .limit(max(0, limit - counts["processed"]))
                .with_for_update(of=bid_reminders, skip_locked=True)
            )
        ).mappings().all()
        for row in rows:
            counts["processed"] += 1
            channel = row["channel"]
            status = "SENT"
            if channel != "IN_APP":
                url = os.environ.get(
                    "BID_REMINDER_EMAIL_WEBHOOK_URL"
                    if channel == "EMAIL"
                    else "BID_REMINDER_WEBHOOK_URL"
                )
                if not url:
                    status = "FAILED"
                else:
                    try:
                        response = await http_client.post(
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
                        )
                        response.raise_for_status()
                    except httpx.HTTPError:
                        status = "FAILED"
            await conn.execute(
                bid_reminders.update()
                .where(bid_reminders.c.id == row["id"])
                .values(
                    status=status,
                    sent_at=now if status == "SENT" else None,
                )
            )
            counts["sent" if status == "SENT" else "failed"] += 1
        if counts["processed"] >= limit:
            break
    await conn.commit()
    return counts
