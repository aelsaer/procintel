from scripts.backfill_cpv_codes import extract_cpv_codes


def test_extract_cpv_codes_preserves_order_and_deduplicates():
    assert extract_cpv_codes(
        {
            "object_details": [
                {"cpv_codes": ["30192110-5", " 79411000-8 "]},
                {"cpv_codes": ["30192110-5", {"key": "72212326-0"}]},
            ]
        }
    ) == ["30192110-5", "79411000-8", "72212326-0"]


def test_extract_cpv_codes_ignores_malformed_details_and_codes():
    assert extract_cpv_codes(
        {
            "object_details": [
                None,
                {"cpv_codes": ["GIS", "123", None, {"label": "bad"}]},
                {"cpv_codes": "77312000-9"},
            ]
        }
    ) == ["77312000-9"]


def test_extract_cpv_codes_rejects_non_object_source_details():
    assert extract_cpv_codes(None) == []
    assert extract_cpv_codes({"object_details": "not-a-list"}) == []
