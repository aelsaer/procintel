from datetime import date, datetime, timezone
from decimal import Decimal

from services.intelligence.eu_matching import (
    countries_for_day,
    cross_border_match,
    date_windows,
    official_ted_url,
)


def test_country_rotation_is_stable_includes_greece_and_covers_all_countries():
    countries = ("GR", "PT", "ES", "IT", "CY")
    first = countries_for_day(date(2026, 7, 31), countries=countries, batch_size=3)
    assert first == countries_for_day(date(2026, 7, 31), countries=countries, batch_size=3)
    assert first[0] == "GR"
    covered = {
        code
        for offset in range(10)
        for code in countries_for_day(date(2026, 7, 1 + offset), countries=countries, batch_size=3)
    }
    assert covered == set(countries)


def test_date_windows_cover_range_without_gaps():
    assert date_windows(date(2026, 6, 1), date(2026, 6, 7), 3) == [
        (date(2026, 6, 1), date(2026, 6, 3)),
        (date(2026, 6, 4), date(2026, 6, 6)),
        (date(2026, 6, 7), date(2026, 6, 7)),
    ]


def test_cross_border_match_requires_specific_keyword_for_broad_cpv():
    score, reasons, barriers, eligible = cross_border_match(
        title="Generic IT infrastructure services",
        cpv_codes=["72000000"],
        profile_cpv_prefixes=["72"],
        profile_keywords=["GIS"],
        amount=Decimal("100000"),
        amount_min=Decimal("10000"),
        amount_max=None,
        deadline=datetime(2026, 8, 30, tzinfo=timezone.utc),
        country_code="PT",
        parse_confidence=0.9,
        as_of=date(2026, 7, 31),
    )
    assert score > 0
    assert reasons
    assert "configured business terms" in barriers[0]
    assert eligible is False


def test_cross_border_match_accepts_keyword_and_returns_operational_barriers():
    score, reasons, barriers, eligible = cross_border_match(
        title="Geographic information system (GIS) for municipal assets",
        cpv_codes=["72212326"],
        profile_cpv_prefixes=["72"],
        profile_keywords=["GIS"],
        amount=Decimal("180000"),
        amount_min=Decimal("10000"),
        amount_max=Decimal("500000"),
        deadline=datetime(2026, 9, 1, tzinfo=timezone.utc),
        country_code="PT",
        parse_confidence=1.0,
        as_of=date(2026, 7, 31),
    )
    assert score == Decimal("100.00")
    assert eligible is True
    assert any("Title fit" in reason for reason in reasons)
    assert any("Cross-border" in barrier for barrier in barriers)
    assert official_ted_url("123-2026", "fallback").endswith("/123-2026")
