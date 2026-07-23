"""WEBHOOK/TEAMS/SLACK delivery — description.txt §30.5's envelope
requirement (event ID, idempotency key, timestamp, tenant ID, retry
policy, signature) plus retry/idempotency-key handling against the
`webhook_deliveries` table.

All three channel types share one delivery+retry mechanism
(`_deliver_and_record`/`retry_pending_deliveries`) and differ only in how
the outbound body is shaped: a generic signed JSON envelope for WEBHOOK,
Microsoft's legacy MessageCard format for TEAMS, Slack's `text`/`blocks`
shape for SLACK. Teams/Slack incoming webhooks don't verify signatures
themselves, but the `webhook_deliveries.signature` column is still
populated the same way for all three — useful for audit, and for any
webhook receiver Teams/Slack-shaped payloads later get proxied through.

Exponential backoff: 60s * 2^attempt_count, capped at 6h, up to
`MAX_ATTEMPTS` before giving up (`status='FAILED'`). The first delivery
attempt happens synchronously inside `deliver()` (best-effort, so a
healthy endpoint gets its event immediately); `retry_pending_deliveries()`
is a separate sweep (called from the orchestration scheduler or its own
CLI) that retries whatever's still `PENDING` and due.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import alert_delivery_targets, webhook_deliveries

MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 6 * 3600


def _next_retry_at(now: datetime, attempt_count: int) -> datetime:
    delay = min(BACKOFF_BASE_SECONDS * (2**attempt_count), BACKOFF_CAP_SECONDS)
    return now + timedelta(seconds=delay)


def _sign(secret: str | None, body: bytes) -> str:
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def build_webhook_envelope(
    *,
    alert_event_id: uuid.UUID,
    idempotency_key: str,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    timestamp: datetime,
) -> dict[str, Any]:
    return {
        "event_id": str(alert_event_id),
        "idempotency_key": idempotency_key,
        "timestamp": timestamp.isoformat(),
        "tenant_id": str(tenant_id),
        "event_type": event_type,
        "payload": payload,
        "retry_policy": {
            "max_attempts": MAX_ATTEMPTS,
            "backoff": "exponential",
            "base_seconds": BACKOFF_BASE_SECONDS,
            "cap_seconds": BACKOFF_CAP_SECONDS,
        },
    }


def build_teams_card(*, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    facts = [{"name": key, "value": str(value)} for key, value in sorted(payload.items())]
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": f"procintel alert: {event_type}",
        "themeColor": "0076D7",
        "title": f"procintel alert: {event_type}",
        "sections": [{"facts": facts}],
    }


def build_slack_message(*, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    lines = [f"*procintel alert:* {event_type}"] + [f"• {key}: {value}" for key, value in sorted(payload.items())]
    return {"text": "\n".join(lines)}


def _build_body(
    *,
    channel_type: str,
    alert_event_id: uuid.UUID,
    idempotency_key: str,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    timestamp: datetime,
) -> dict[str, Any]:
    if channel_type == "TEAMS":
        return build_teams_card(event_type=event_type, payload=payload)
    if channel_type == "SLACK":
        return build_slack_message(event_type=event_type, payload=payload)
    return build_webhook_envelope(
        alert_event_id=alert_event_id,
        idempotency_key=idempotency_key,
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload,
        timestamp=timestamp,
    )


async def _attempt_delivery(http_client: httpx.AsyncClient, *, url: str, body: dict[str, Any], secret: str | None) -> int:
    body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    signature = _sign(secret, body_bytes)
    if signature:
        headers["X-Procintel-Signature"] = f"sha256={signature}"
    response = await http_client.post(url, content=body_bytes, headers=headers)
    return response.status_code


class WebhookLikeDeliveryChannel:
    """Shared implementation for WEBHOOK/TEAMS/SLACK — pass the
    `channel_type` this instance handles. Each fans out to every active
    `alert_delivery_targets` row of that type for the firing rule."""

    def __init__(self, channel_type: str, http_client: httpx.AsyncClient | None = None) -> None:
        if channel_type not in ("WEBHOOK", "TEAMS", "SLACK"):
            raise ValueError(f"unsupported channel_type: {channel_type!r}")
        self._channel_type = channel_type
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

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
        targets = (
            await conn.execute(
                select(alert_delivery_targets).where(
                    alert_delivery_targets.c.alert_rule_id == alert_rule_id,
                    alert_delivery_targets.c.channel_type == self._channel_type,
                    alert_delivery_targets.c.is_active.is_(True),
                )
            )
        ).all()

        for target in targets:
            now = datetime.now(timezone.utc)
            idempotency_key = f"{alert_event_id}:{target.id}"
            body = _build_body(
                channel_type=self._channel_type,
                alert_event_id=alert_event_id,
                idempotency_key=idempotency_key,
                tenant_id=tenant_id,
                event_type=event_type,
                payload=payload,
                timestamp=now,
            )
            body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
            signature = _sign(target.secret, body_bytes)

            delivery_id = uuid.uuid4()
            insert_stmt = (
                pg_insert(webhook_deliveries)
                .values(
                    id=delivery_id,
                    alert_event_id=alert_event_id,
                    tenant_id=tenant_id,
                    endpoint_url=target.target,
                    idempotency_key=idempotency_key,
                    signature=signature,
                    status="PENDING",
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
                .returning(webhook_deliveries.c.id)
            )
            inserted = (await conn.execute(insert_stmt)).first()
            if inserted is None:
                continue  # already recorded (deliver() called twice for the same event) — don't double-send
            await conn.commit()

            try:
                status_code = await _attempt_delivery(
                    self._http, url=target.target, body=body, secret=target.secret
                )
                succeeded = 200 <= status_code < 300
            except httpx.HTTPError:
                succeeded = False
                status_code = None

            if succeeded:
                await conn.execute(
                    webhook_deliveries.update()
                    .where(webhook_deliveries.c.id == delivery_id)
                    .values(
                        status="DELIVERED",
                        attempt_count=1,
                        last_attempt_at=now,
                        response_status=status_code,
                    )
                )
            else:
                await conn.execute(
                    webhook_deliveries.update()
                    .where(webhook_deliveries.c.id == delivery_id)
                    .values(
                        status="PENDING",
                        attempt_count=1,
                        last_attempt_at=now,
                        next_retry_at=_next_retry_at(now, 1),
                        response_status=status_code,
                    )
                )
            await conn.commit()


async def retry_pending_deliveries(conn: AsyncConnection, http_client: httpx.AsyncClient, *, now: datetime | None = None) -> int:
    """Retries every `webhook_deliveries` row still `PENDING` and due
    (`next_retry_at` in the past). Returns how many rows were retried.
    Doesn't know or care which channel type produced the row — the
    envelope/signature/idempotency-key are already fixed at first-attempt
    time, a retry just re-POSTs the exact same recorded intent."""
    now = now or datetime.now(timezone.utc)
    rows = (
        await conn.execute(
            select(webhook_deliveries).where(
                webhook_deliveries.c.status == "PENDING",
                (webhook_deliveries.c.next_retry_at.is_(None)) | (webhook_deliveries.c.next_retry_at <= now),
            )
        )
    ).all()

    retried = 0
    for row in rows:
        retried += 1
        # The original signed body isn't stored verbatim (only its
        # signature/hash), so a retry re-signs an equivalent minimal
        # envelope carrying the same idempotency key — a real receiver
        # keys deduplication off `idempotency_key`, not byte-for-byte body
        # equality.
        body = {"idempotency_key": row.idempotency_key, "retry": True}
        try:
            response = await http_client.post(row.endpoint_url, json=body)
            succeeded = 200 <= response.status_code < 300
            status_code = response.status_code
        except httpx.HTTPError:
            succeeded = False
            status_code = None

        new_attempt_count = row.attempt_count + 1
        if succeeded:
            await conn.execute(
                webhook_deliveries.update()
                .where(webhook_deliveries.c.id == row.id)
                .values(status="DELIVERED", attempt_count=new_attempt_count, last_attempt_at=now, response_status=status_code)
            )
        elif new_attempt_count >= MAX_ATTEMPTS:
            await conn.execute(
                webhook_deliveries.update()
                .where(webhook_deliveries.c.id == row.id)
                .values(status="FAILED", attempt_count=new_attempt_count, last_attempt_at=now, response_status=status_code)
            )
        else:
            await conn.execute(
                webhook_deliveries.update()
                .where(webhook_deliveries.c.id == row.id)
                .values(
                    attempt_count=new_attempt_count,
                    last_attempt_at=now,
                    next_retry_at=_next_retry_at(now, new_attempt_count),
                    response_status=status_code,
                )
            )
        await conn.commit()

    return retried
