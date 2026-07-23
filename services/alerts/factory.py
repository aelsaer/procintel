"""Production alert-delivery channel construction."""

from __future__ import annotations

import httpx

from .delivery import MultiplexingDeliveryChannel
from .email_delivery import EmailDeliveryChannel
from .webhook_delivery import WebhookLikeDeliveryChannel


def build_delivery_channel(
    http_client: httpx.AsyncClient | None = None,
) -> MultiplexingDeliveryChannel:
    """Build all supported channels.

    Each implementation queries the active targets for its own channel type,
    so unused channels are no-ops and require no provider configuration.
    """
    return MultiplexingDeliveryChannel(
        [
            EmailDeliveryChannel(),
            WebhookLikeDeliveryChannel("WEBHOOK", http_client),
            WebhookLikeDeliveryChannel("TEAMS", http_client),
            WebhookLikeDeliveryChannel("SLACK", http_client),
        ]
    )
