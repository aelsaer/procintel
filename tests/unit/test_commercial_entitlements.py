import hashlib
import hmac
from pathlib import Path

from services.product.entitlements import (
    effective_entitlements,
    monthly_period,
    usage_permitted,
    verify_stripe_signature,
)


def test_entitlement_override_and_unlimited_usage():
    assert effective_entitlements({"users": 2, "api": False}, {"users": 5}) == {
        "users": 5,
        "api": False,
    }
    assert usage_permitted(-1, 1_000_000)
    assert not usage_permitted(20, 20)


def test_monthly_period_uses_exclusive_next_month_boundary():
    start, end = monthly_period(__import__("datetime").date(2026, 12, 15))
    assert str(start) == "2026-12-01"
    assert str(end) == "2027-01-01"


def test_stripe_signature_verification_checks_hmac_and_age():
    payload = b'{"id":"evt_1"}'
    timestamp = 1_800_000_000
    digest = hmac.new(
        b"whsec_test",
        str(timestamp).encode() + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    header = f"t={timestamp},v1={digest}"
    assert verify_stripe_signature(payload, header, "whsec_test", now=timestamp)
    assert not verify_stripe_signature(payload, header, "wrong", now=timestamp)
    assert not verify_stripe_signature(payload, header, "whsec_test", now=timestamp + 301)


def test_dev_database_dependency_provisions_professional_subscription():
    source = (
        Path(__file__).resolve().parents[2] / "apps" / "api" / "db.py"
    ).read_text(encoding="utf-8")
    assert "INSERT INTO tenant_subscriptions" in source
    assert "'PROFESSIONAL', 'ACTIVE'" in source
    assert "ON CONFLICT (tenant_id) DO NOTHING" in source
