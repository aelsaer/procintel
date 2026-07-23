"""Pure envelope/card-building and backoff logic in webhook_delivery.py —
no DB, no network. `deliver()`/`retry_pending_deliveries()` themselves
need a real Postgres instance (webhook_deliveries writes) — see
tests/integration/test_alerts_delivery_channels_db.py.
"""

import uuid
from datetime import datetime, timedelta, timezone

from services.alerts.webhook_delivery import (
    BACKOFF_CAP_SECONDS,
    MAX_ATTEMPTS,
    _next_retry_at,
    _sign,
    build_slack_message,
    build_teams_card,
    build_webhook_envelope,
)

ALERT_EVENT_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()
NOW = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_webhook_envelope_has_every_required_30_5_field():
    envelope = build_webhook_envelope(
        alert_event_id=ALERT_EVENT_ID,
        idempotency_key="abc123",
        tenant_id=TENANT_ID,
        event_type="contract.created",
        payload={"amount": "1000"},
        timestamp=NOW,
    )
    assert envelope["event_id"] == str(ALERT_EVENT_ID)
    assert envelope["idempotency_key"] == "abc123"
    assert envelope["tenant_id"] == str(TENANT_ID)
    assert envelope["timestamp"] == NOW.isoformat()
    assert envelope["event_type"] == "contract.created"
    assert envelope["payload"] == {"amount": "1000"}
    assert envelope["retry_policy"]["max_attempts"] == MAX_ATTEMPTS


def test_teams_card_is_messagecard_shaped():
    card = build_teams_card(event_type="contract.modified", payload={"status": "ACTIVE"})
    assert card["@type"] == "MessageCard"
    assert "contract.modified" in card["title"]
    assert card["sections"][0]["facts"] == [{"name": "status", "value": "ACTIVE"}]


def test_slack_message_has_text_field():
    message = build_slack_message(event_type="payment.detected", payload={"amount": "500"})
    assert "payment.detected" in message["text"]
    assert "amount" in message["text"]


def test_sign_is_deterministic_hmac_sha256():
    body = b'{"a":1}'
    sig1 = _sign("shared-secret", body)
    sig2 = _sign("shared-secret", body)
    assert sig1 == sig2
    assert len(sig1) == 64  # hex-encoded sha256 digest


def test_sign_without_a_secret_returns_empty_string():
    assert _sign(None, b"body") == ""
    assert _sign("", b"body") == ""


def test_different_secrets_produce_different_signatures():
    body = b"same body"
    assert _sign("secret-a", body) != _sign("secret-b", body)


def test_next_retry_backoff_grows_exponentially_and_is_capped():
    delays = [(_next_retry_at(NOW, n) - NOW).total_seconds() for n in range(0, 12)]
    assert delays[0] == 60
    assert delays[1] == 120
    assert delays[2] == 240
    assert all(d <= BACKOFF_CAP_SECONDS for d in delays)
    assert delays[-1] == BACKOFF_CAP_SECONDS  # eventually hits the cap
