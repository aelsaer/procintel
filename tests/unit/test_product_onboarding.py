from services.product.onboarding import (
    normalize_cpv_codes,
    normalize_terms,
    profile_quality,
    rank_initial_opportunities,
)


def test_profile_inputs_are_normalized_without_broadening_cpv_scope():
    assert normalize_cpv_codes(["77312000-9", " 72 ", "77312000", "bad"]) == [
        "77312000",
        "72",
    ]
    assert normalize_terms([" GIS ", "gis", "γεωγραφικά   συστήματα"]) == [
        "GIS",
        "γεωγραφικά συστήματα",
    ]


def test_quality_requires_confirmed_cpv_and_rewards_real_results():
    score, findings = profile_quality(
        description="Παρέχουμε υπηρεσίες γεωπληροφορικής GIS και ανάπτυξη εφαρμογών για δημόσιους φορείς",
        cpv_codes=[],
        keywords=["GIS"],
        opportunity_count=0,
    )
    assert score < 50
    assert {finding["code"] for finding in findings} >= {
        "NO_CPV_CONFIRMED",
        "LOW_INITIAL_COVERAGE",
    }

    complete_score, complete_findings = profile_quality(
        description=(
            "Παρέχουμε μελέτες γεωπληροφορικής, ανάπτυξη GIS εφαρμογών, "
            "χαρτογραφικές υπηρεσίες και υποστήριξη χωρικών βάσεων δεδομένων "
            "σε δήμους και οργανισμούς του δημόσιου τομέα"
        ),
        cpv_codes=["72212326", "71354100"],
        keywords=["GIS", "γεωπληροφορική"],
        opportunity_count=10,
    )
    assert complete_score >= 85
    assert not any(item["severity"] == "ERROR" for item in complete_findings)


def test_initial_opportunities_are_unique_ranked_and_capped_at_ten():
    rows = [
        {
            "process_id": str(index),
            "score": index,
            "data_confidence": 70,
            "recency_rank": index,
        }
        for index in range(12)
    ]
    rows.append({"process_id": "11", "score": 5, "data_confidence": 100})
    ranked = rank_initial_opportunities(rows)
    assert len(ranked) == 10
    assert ranked[0]["process_id"] == "11"
    assert len({item["process_id"] for item in ranked}) == 10
