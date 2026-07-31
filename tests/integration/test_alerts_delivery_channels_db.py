"""Real EMAIL/WEBHOOK delivery end-to-end against a real Postgres
instance: `evaluate_and_fire` -> `MultiplexingDeliveryChannel` ->
`EmailDeliveryChannel` (smtplib mocked) + `WebhookLikeDeliveryChannel`
("WEBHOOK", HTTP mocked via respx) -> a real `webhook_deliveries` row.
Also exercises `retry_pending_deliveries` directly against a manually
inserted PENDING row.

Skipped automatically unless $DATABASE_URL is set.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    alert_delivery_targets,
    alert_events,
    alert_rules,
    tenants,
    users,
    webhook_deliveries,
)
from services.alerts.delivery import MultiplexingDeliveryChannel
from services.alerts.email_delivery import EmailDeliveryChannel, SmtpConfig
from services.alerts.evaluate import evaluate_company_status_change_and_fire
from services.alerts.webhook_delivery import MAX_ATTEMPTS, WebhookLikeDeliveryChannel, retry_pending_deliveries

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")

WEBHOOK_URL = "https://example.test/incoming-webhook"


def _asyncpg_url() -> str:
    url = DATABASE_URL
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


class _FakeSmtp:
    sent = []

    def __init__(self, host, port, timeout=10):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context=None):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        _FakeSmtp.sent.append(message)


async def _seed_rule_with_targets(
    conn,
    *,
    entity_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await conn.execute(tenants.insert().values(id=tenant_id, name="Test Tenant"))
    await conn.execute(users.insert().values(id=user_id, email=f"{uuid.uuid4()}@example.test"))
    rule_id = uuid.uuid4()
    await conn.execute(
        alert_rules.insert().values(
            id=rule_id,
            tenant_id=tenant_id,
            user_id=user_id,
            name="delivery channel test",
            event_types=["company.status_changed"],
            filters=(
                {"supplier_id": str(entity_id)}
                if entity_id is not None
                else {}
            ),
            schedule="IMMEDIATE",
            delivery_channels=["EMAIL", "WEBHOOK"],
        )
    )
    await conn.execute(
        alert_delivery_targets.insert().values(
            id=uuid.uuid4(), alert_rule_id=rule_id, channel_type="EMAIL", target="analyst@example.test"
        )
    )
    await conn.execute(
        alert_delivery_targets.insert().values(
            id=uuid.uuid4(),
            alert_rule_id=rule_id,
            channel_type="WEBHOOK",
            target=WEBHOOK_URL,
            secret="shared-secret",
        )
    )
    await conn.commit()
    return tenant_id, rule_id


@respx.mock
async def test_multiplexing_channel_delivers_email_and_webhook_and_records_webhook_deliveries(monkeypatch):
    _FakeSmtp.sent = []
    monkeypatch.setattr("smtplib.SMTP", _FakeSmtp)
    respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))

    engine = create_async_engine(_asyncpg_url())
    channel = MultiplexingDeliveryChannel(
        [
            EmailDeliveryChannel(SmtpConfig(host="smtp.example.test")),
            WebhookLikeDeliveryChannel("WEBHOOK"),
        ]
    )
    try:
        async with engine.connect() as conn:
            entity_id = uuid.uuid4()
            tenant_id, rule_id = await _seed_rule_with_targets(
                conn,
                entity_id=entity_id,
            )
            try:
                fired = await evaluate_company_status_change_and_fire(
                    conn,
                    entity_id=entity_id,
                    old_status="ACTIVE",
                    new_status="IN_LIQUIDATION",
                    delivery_channel=channel,
                )
                assert fired >= 1
                own_events = (
                    await conn.execute(
                        select(alert_events).where(
                            alert_events.c.alert_rule_id == rule_id
                        )
                    )
                ).all()
                assert len(own_events) == 1

                assert len(_FakeSmtp.sent) == 1
                assert _FakeSmtp.sent[0]["To"] == "analyst@example.test"

                delivery_rows = (
                    await conn.execute(
                        select(webhook_deliveries).where(webhook_deliveries.c.tenant_id == tenant_id)
                    )
                ).all()
                assert len(delivery_rows) == 1
                assert delivery_rows[0].status == "DELIVERED"
                assert delivery_rows[0].response_status == 200
                assert delivery_rows[0].endpoint_url == WEBHOOK_URL
                assert delivery_rows[0].signature  # non-empty — a secret was configured
            finally:
                await conn.execute(webhook_deliveries.delete().where(webhook_deliveries.c.tenant_id == tenant_id))
                await conn.execute(alert_events.delete().where(alert_events.c.alert_rule_id == rule_id))
                await conn.commit()
    finally:
        await engine.dispose()


@respx.mock
async def test_retry_pending_deliveries_succeeds_and_marks_delivered():
    route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            tenant_id, rule_id = await _seed_rule_with_targets(conn)
            alert_event_id = uuid.uuid4()
            await conn.execute(
                alert_events.insert().values(
                    id=alert_event_id,
                    alert_rule_id=rule_id,
                    canonical_object_type="entities",
                    canonical_object_id=uuid.uuid4(),
                    event_type="company.status_changed",
                    material_change_hash="hash1",
                    payload={},
                )
            )
            delivery_id = uuid.uuid4()
            await conn.execute(
                webhook_deliveries.insert().values(
                    id=delivery_id,
                    alert_event_id=alert_event_id,
                    tenant_id=tenant_id,
                    endpoint_url=WEBHOOK_URL,
                    idempotency_key=f"{alert_event_id}:retry-test",
                    signature="deadbeef",
                    status="PENDING",
                    attempt_count=1,
                    next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                )
            )
            await conn.commit()
            try:
                async with httpx.AsyncClient() as client:
                    retried = await retry_pending_deliveries(conn, client)
                assert retried == 1
                assert route.called

                row = (
                    await conn.execute(select(webhook_deliveries).where(webhook_deliveries.c.id == delivery_id))
                ).one()
                assert row.status == "DELIVERED"
                assert row.attempt_count == 2
            finally:
                await conn.execute(webhook_deliveries.delete().where(webhook_deliveries.c.id == delivery_id))
                await conn.execute(alert_events.delete().where(alert_events.c.id == alert_event_id))
                await conn.commit()
    finally:
        await engine.dispose()


@respx.mock
async def test_retry_pending_deliveries_gives_up_after_max_attempts():
    respx.post("https://example.test/permanently-failing-endpoint").mock(return_value=httpx.Response(500))
    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            tenant_id, rule_id = await _seed_rule_with_targets(conn)
            alert_event_id = uuid.uuid4()
            await conn.execute(
                alert_events.insert().values(
                    id=alert_event_id,
                    alert_rule_id=rule_id,
                    canonical_object_type="entities",
                    canonical_object_id=uuid.uuid4(),
                    event_type="company.status_changed",
                    material_change_hash="hash2",
                    payload={},
                )
            )
            delivery_id = uuid.uuid4()
            await conn.execute(
                webhook_deliveries.insert().values(
                    id=delivery_id,
                    alert_event_id=alert_event_id,
                    tenant_id=tenant_id,
                    endpoint_url="https://example.test/permanently-failing-endpoint",
                    idempotency_key=f"{alert_event_id}:retry-test-2",
                    signature="deadbeef",
                    status="PENDING",
                    attempt_count=MAX_ATTEMPTS - 1,
                    next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                )
            )
            await conn.commit()
            try:
                async with httpx.AsyncClient() as client:
                    await retry_pending_deliveries(conn, client)

                row = (
                    await conn.execute(select(webhook_deliveries).where(webhook_deliveries.c.id == delivery_id))
                ).one()
                assert row.status == "FAILED"
                assert row.attempt_count == MAX_ATTEMPTS
            finally:
                await conn.execute(webhook_deliveries.delete().where(webhook_deliveries.c.id == delivery_id))
                await conn.execute(alert_events.delete().where(alert_events.c.id == alert_event_id))
                await conn.commit()
    finally:
        await engine.dispose()
