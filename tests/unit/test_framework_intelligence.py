from datetime import date, timedelta
from decimal import Decimal

from services.intelligence.frameworks import framework_relevance_score, framework_window_status


def test_framework_window_status_distinguishes_reopening_and_expired():
    today = date(2026, 7, 31)
    assert framework_window_status(today - timedelta(days=1), today=today) == "EXPIRED"
    assert framework_window_status(today + timedelta(days=90), today=today) == "REOPENING"
    assert framework_window_status(today + timedelta(days=365), today=today) == "ACTIVE"
    assert framework_window_status(None, today=today) == "UNKNOWN"


def test_relevance_rewards_exact_market_fit_and_realized_spend():
    strong = framework_relevance_score(
        cpv_codes=["77312000"],
        profile_cpv_prefixes=["77312000"],
        realized_spend=Decimal("800000"),
        ceiling_amount=Decimal("1000000"),
        status="REOPENING",
    )
    weak = framework_relevance_score(
        cpv_codes=["72000000"],
        profile_cpv_prefixes=["77312000"],
        realized_spend=0,
        ceiling_amount=Decimal("1000000"),
        status="EXPIRED",
    )
    assert strong > 90
    assert weak < 10


def test_unscoped_profile_can_still_rank_route_to_market_activity():
    score = framework_relevance_score(
        cpv_codes=["72000000"],
        profile_cpv_prefixes=[],
        realized_spend=250,
        ceiling_amount=1000,
        status="ACTIVE",
    )
    assert score > 75
