from datetime import datetime, timezone

import pytest

from services.ingestion.orchestration.cli import next_daily_run, parse_daily_schedule


def test_next_daily_run_uses_configured_local_timezone():
    schedule = parse_daily_schedule("02:30", "Europe/Athens")
    now = datetime(2026, 7, 23, 20, 0, tzinfo=timezone.utc)

    next_run = next_daily_run(now, schedule)

    assert next_run.isoformat() == "2026-07-24T02:30:00+03:00"


def test_next_daily_run_uses_today_when_time_has_not_passed():
    schedule = parse_daily_schedule("14:15", "Europe/Athens")
    now = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)

    next_run = next_daily_run(now, schedule)

    assert next_run.isoformat() == "2026-07-23T14:15:00+03:00"


@pytest.mark.parametrize("at", ["tomorrow", "25:00", "12:75"])
def test_invalid_daily_time_is_rejected(at):
    with pytest.raises(ValueError):
        parse_daily_schedule(at, "Europe/Athens")


def test_invalid_timezone_is_rejected():
    with pytest.raises(ValueError):
        parse_daily_schedule("02:30", "Mars/Olympus")
