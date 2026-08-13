from datetime import datetime, timezone

from services.bids.reminders import RETRY_CAP_SECONDS, _next_retry_at


def test_reminder_retry_uses_bounded_exponential_backoff():
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)

    assert (_next_retry_at(now, 1) - now).total_seconds() == 60
    assert (_next_retry_at(now, 4) - now).total_seconds() == 480
    assert (_next_retry_at(now, 99) - now).total_seconds() == RETRY_CAP_SECONDS
