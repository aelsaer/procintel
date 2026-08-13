"""Delivery channel abstraction — description.txt §32.

`DeliveryChannel.deliver()` takes the DB connection alongside the event
details because every real implementation needs it: `EmailDeliveryChannel`/
`WebhookLikeDeliveryChannel` (`email_delivery.py`/`webhook_delivery.py`)
look up the firing rule's concrete destinations from
`alert_delivery_targets`, and the webhook-shaped channels additionally
write `webhook_deliveries` rows for retry/idempotency bookkeeping (§30.5).
`LogDeliveryChannel` ignores `conn` — it's kept purely so
`evaluate.py`/tests can exercise the rule-matching/dedup core without any
of EMAIL/WEBHOOK/TEAMS/SLACK's infra.

`MultiplexingDeliveryChannel` is what real callers use: it holds one
instance of each real channel and calls all of them for every event — each
one independently no-ops if the firing rule has no active
`alert_delivery_targets` row of its type, so "this rule only has an EMAIL
target" naturally means only `EmailDeliveryChannel` does anything.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncConnection


class DeliveryChannel(Protocol):
    async def deliver(
        self,
        conn: AsyncConnection,
        *,
        alert_rule_id: uuid.UUID,
        tenant_id: uuid.UUID,
        alert_event_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...


class LogDeliveryChannel:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("procintel.alerts")

    async def deliver(
        self,
        conn: AsyncConnection,
        *,
        alert_rule_id: uuid.UUID,
        tenant_id: uuid.UUID,
        alert_event_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self._logger.info(
            "alert fired: rule=%s event=%s type=%s payload=%s", alert_rule_id, alert_event_id, event_type, payload
        )


class MultiplexingDeliveryChannel:
    """Fans out to every channel instance given — real production use
    passes `[EmailDeliveryChannel(), WebhookLikeDeliveryChannel("WEBHOOK"),
    WebhookLikeDeliveryChannel("TEAMS"), WebhookLikeDeliveryChannel("SLACK")]`.
    A failure in one channel is logged and doesn't stop the others from
    attempting delivery — one dead SMTP server shouldn't also silently
    swallow a working webhook target for the same event."""

    def __init__(self, channels: list[DeliveryChannel], logger: logging.Logger | None = None) -> None:
        self._channels = channels
        self._logger = logger or logging.getLogger("procintel.alerts")

    async def deliver(
        self,
        conn: AsyncConnection,
        *,
        alert_rule_id: uuid.UUID,
        tenant_id: uuid.UUID,
        alert_event_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        errors: list[str] = []
        for channel in self._channels:
            try:
                await channel.deliver(
                    conn,
                    alert_rule_id=alert_rule_id,
                    tenant_id=tenant_id,
                    alert_event_id=alert_event_id,
                    event_type=event_type,
                    payload=payload,
                )
            except Exception:  # noqa: BLE001 — one channel's failure must not block the others
                self._logger.exception(
                    "delivery channel %s failed for alert_event=%s", type(channel).__name__, alert_event_id
                )
                errors.append(type(channel).__name__)
        if errors:
            raise DeliveryChannelError(errors)


class DeliveryChannelError(RuntimeError):
    def __init__(self, failed_channels: list[str]) -> None:
        self.failed_channels = tuple(failed_channels)
        super().__init__(f"delivery failed for channel(s): {', '.join(failed_channels)}")
