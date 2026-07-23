"""SMTP email delivery — description.txt §5.8/§32's EMAIL channel.

Uses the standard-library `smtplib`, not a new dependency, wrapped in
`asyncio.to_thread` the same way `services/documents/ocr.py` wraps the
blocking `subprocess.run` call — consistent pattern for "this one
operation is synchronous, everything around it isn't."
"""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import alert_delivery_targets


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    from_address: str = "alerts@procintel.example"
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> "SmtpConfig":
        host = os.environ.get("SMTP_HOST")
        if not host:
            raise RuntimeError("SMTP_HOST is not set — required for EMAIL alert delivery")
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ.get("SMTP_USERNAME"),
            password=os.environ.get("SMTP_PASSWORD"),
            from_address=os.environ.get("SMTP_FROM_ADDRESS", "alerts@procintel.example"),
            use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() != "false",
        )


def _format_email(*, event_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    subject = f"[procintel] {event_type}"
    lines = [f"{key}: {value}" for key, value in sorted(payload.items())]
    body = "\n".join(lines)
    return subject, body


def _send_sync(config: SmtpConfig, *, to_address: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.from_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(config.host, config.port, timeout=10) as smtp:
        if config.use_tls:
            smtp.starttls(context=ssl.create_default_context())
        if config.username and config.password:
            smtp.login(config.username, config.password)
        smtp.send_message(message)


class EmailDeliveryChannel:
    """Sends one email per active EMAIL `alert_delivery_targets` row for
    the firing rule. A missing/misconfigured SMTP server raises rather
    than silently dropping the alert — callers decide whether to catch it
    (e.g. the multiplexer logs and continues with other channels)."""

    def __init__(self, config: SmtpConfig | None = None) -> None:
        self._config = config

    def _resolve_config(self) -> SmtpConfig:
        return self._config or SmtpConfig.from_env()

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
                    alert_delivery_targets.c.channel_type == "EMAIL",
                    alert_delivery_targets.c.is_active.is_(True),
                )
            )
        ).all()
        if not targets:
            return

        config = self._resolve_config()
        subject, body = _format_email(event_type=event_type, payload=payload)
        for target in targets:
            await asyncio.to_thread(_send_sync, config, to_address=target.target, subject=subject, body=body)
