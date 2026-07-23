from datetime import date
from decimal import Decimal

from services.exports.generate import _scope_params


def test_export_scope_normalizes_cross_tab_profile_filters():
    params = _scope_params({
        "cpv_prefixes": "77312000,77312100",
        "keywords": "αποψιλ,καθαρισμ",
        "nuts_code": "el30",
        "municipality": "Αθήνα",
        "date_from": "2026-06-01",
        "date_to": "2026-06-30",
        "amount_min": 10_000,
    })

    assert params["cpv_likes"] == ["77312000%", "77312100%"]
    assert len(params["keyword_patterns"]) == 2
    assert all(pattern.startswith("(?=.*") for pattern in params["keyword_patterns"])
    assert params["taxonomy_match_mode"] == "ANY"
    assert params["date_from"] == date(2026, 6, 1)
    assert params["date_to"] == date(2026, 6, 30)
    assert params["amount_min"] == Decimal("10000")
    assert params["nuts"] == "EL30"
    assert params["municipality_like"] == "%Αθήνα%"


def test_export_scope_requires_lexical_intent_when_profile_has_keywords():
    params = _scope_params({
        "cpv_prefixes": ["72"],
        "keywords": ["GIS"],
        "taxonomy_match": "KEYWORD_REQUIRED",
    })

    assert params["cpv_likes"] == []
    assert params["keyword_patterns"] == [
        r"(?=.*(^| )((arc|q|web)?gis)( |$)).*"
    ]
