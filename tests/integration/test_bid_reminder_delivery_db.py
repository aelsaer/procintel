from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    bid_reminders,
    bid_workspaces,
    procurement_processes,
    tenants,
    users,
)
from services.bids.reminders import deliver_due_reminders

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_failed_reminder_is_retried_and_delivered(monkeypatch):
    engine = create_async_engine(_async_url())
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    process_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    reminder_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    responses = iter((503, 204))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(responses), request=request)

    monkeypatch.setenv("BID_REMINDER_WEBHOOK_URL", "https://delivery.example.test/reminders")
    try:
        async with engine.begin() as conn:
            await conn.execute(tenants.insert().values(id=tenant_id, name="Reminder delivery"))
            await conn.execute(
                users.insert().values(
                    id=user_id,
                    email=f"reminder-{user_id}@example.test",
                )
            )
            await conn.execute(
                procurement_processes.insert().values(
                    id=process_id,
                    public_id=f"reminder-{process_id}",
                    title="Reminder process",
                )
            )
            await conn.execute(
                bid_workspaces.insert().values(
                    id=workspace_id,
                    tenant_id=tenant_id,
                    process_id=process_id,
                    owner_user_id=user_id,
                )
            )
            await conn.execute(
                bid_reminders.insert().values(
                    id=reminder_id,
                    tenant_id=tenant_id,
                    bid_workspace_id=workspace_id,
                    assigned_user_id=user_id,
                    remind_at=now,
                    channel="WEBHOOK",
                    created_by=user_id,
                )
            )

        async with engine.connect() as conn, httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            first = await deliver_due_reminders(conn, client, now=now)
            assert first["failed"] >= 1
            row = (
                await conn.execute(
                    sa.select(bid_reminders).where(bid_reminders.c.id == reminder_id)
                )
            ).one()
            assert row.status == "PENDING"
            assert row.attempt_count == 1
            assert row.next_retry_at is not None

            second = await deliver_due_reminders(conn, client, now=row.next_retry_at)
            assert second["sent"] >= 1
            row = (
                await conn.execute(
                    sa.select(bid_reminders).where(bid_reminders.c.id == reminder_id)
                )
            ).one()
            assert row.status == "SENT"
            assert row.attempt_count == 2
            assert row.sent_at is not None
    finally:
        async with engine.begin() as conn:
            await conn.execute(bid_reminders.delete().where(bid_reminders.c.id == reminder_id))
            await conn.execute(bid_workspaces.delete().where(bid_workspaces.c.id == workspace_id))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id == process_id))
            await conn.execute(users.delete().where(users.c.id == user_id))
            await conn.execute(tenants.delete().where(tenants.c.id == tenant_id))
        await engine.dispose()
