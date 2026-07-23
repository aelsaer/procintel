from datetime import date
from decimal import Decimal

from services.ingestion.connectors.anaptyxi.resolve import (
    _period_overlaps,
    _try_level2_afm_title_period,
    _try_level3_ada_adam_in_metadata,
    _try_level4_fuzzy,
)

PROJECT_A = {
    "misCode": "MIS-A",
    "title": "Ψηφιακός Μετασχηματισμός Δήμου Δοκιμής",
    "startDate": "2023-01-01",
    "endDate": "2025-12-31",
    "budget": 500000.00,
    "nutsCode": "EL301",
}

PROJECT_B = {
    "misCode": "MIS-B",
    "title": "Εντελώς Άσχετο Έργο Αποχέτευσης",
    "startDate": "2020-01-01",
    "endDate": "2021-12-31",
    "budget": 50000.00,
    "nutsCode": "EL432",
}


def test_period_overlaps_within_slack():
    assert _period_overlaps(date(2023, 6, 1), "2023-01-01", "2025-12-31", 60) is True


def test_period_overlaps_outside_slack_rejected():
    assert _period_overlaps(date(2030, 1, 1), "2023-01-01", "2025-12-31", 60) is False


def test_period_overlaps_missing_act_date_is_false():
    assert _period_overlaps(None, "2023-01-01", "2025-12-31", 60) is False


def test_level2_matches_single_candidate_by_title_and_period():
    result = _try_level2_afm_title_period(
        [PROJECT_A, PROJECT_B],
        act_title="Ψηφιακός Μετασχηματισμός Δήμου Δοκιμής",
        act_date=date(2023, 6, 1),
    )
    assert result is not None
    raw_project, score = result
    assert raw_project["misCode"] == "MIS-A"
    assert score >= 0.5


def test_level2_no_match_when_title_dissimilar():
    result = _try_level2_afm_title_period(
        [PROJECT_B], act_title="Ψηφιακός Μετασχηματισμός Δήμου Δοκιμής", act_date=date(2020, 6, 1)
    )
    assert result is None


def test_level2_ambiguous_multiple_candidates_returns_none():
    duplicate = dict(PROJECT_A, misCode="MIS-A2")
    result = _try_level2_afm_title_period(
        [PROJECT_A, duplicate],
        act_title="Ψηφιακός Μετασχηματισμός Δήμου Δοκιμής",
        act_date=date(2023, 6, 1),
    )
    assert result is None


def test_level3_matches_on_ada_containment():
    project_with_ada = dict(PROJECT_A, relatedAda="6Ω0Ζ465ΦΘΘ-ΔΕΖ")
    result = _try_level3_ada_adam_in_metadata(
        [project_with_ada, PROJECT_B], related_ada_candidates=["6Ω0Ζ465ΦΘΘ-ΔΕΖ"]
    )
    assert result is not None
    raw_project, matched_ada = result
    assert raw_project["misCode"] == "MIS-A"
    assert matched_ada == "6Ω0Ζ465ΦΘΘ-ΔΕΖ"


def test_level3_no_candidates_list_returns_none():
    assert _try_level3_ada_adam_in_metadata([PROJECT_A], related_ada_candidates=[]) is None


def test_level3_no_ada_present_anywhere_returns_none():
    result = _try_level3_ada_adam_in_metadata([PROJECT_A, PROJECT_B], related_ada_candidates=["NONEXISTENT-ADA"])
    assert result is None


def test_level4_matches_on_looser_title_and_amount_tolerance():
    result = _try_level4_fuzzy(
        [PROJECT_A, PROJECT_B],
        act_title="Μετασχηματισμός Δήμου Δοκιμής (ψηφιακός)",
        act_amount=Decimal("510000.00"),
        act_region="EL301",
    )
    assert result is not None
    raw_project, score = result
    assert raw_project["misCode"] == "MIS-A"


def test_level4_rejects_amount_outside_tolerance():
    result = _try_level4_fuzzy(
        [PROJECT_A],
        act_title="Ψηφιακός Μετασχηματισμός Δήμου Δοκιμής",
        act_amount=Decimal("900000.00"),
        act_region=None,
    )
    assert result is None


def test_level4_rejects_conflicting_region():
    result = _try_level4_fuzzy(
        [PROJECT_A],
        act_title="Ψηφιακός Μετασχηματισμός Δήμου Δοκιμής",
        act_amount=None,
        act_region="EL999",
    )
    assert result is None


def test_level4_missing_region_on_either_side_does_not_block():
    result = _try_level4_fuzzy(
        [PROJECT_A], act_title="Ψηφιακός Μετασχηματισμός Δήμου Δοκιμής", act_amount=None, act_region=None
    )
    assert result is not None
