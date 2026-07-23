from services.entity_resolution.text_similarity import normalized_similarity


def test_identical_strings_score_one():
    assert normalized_similarity("ΔΗΜΟΣ ΔΟΚΙΜΗΣ", "ΔΗΜΟΣ ΔΟΚΙΜΗΣ") == 1.0


def test_case_and_whitespace_insensitive():
    assert normalized_similarity("  δημος δοκιμης  ", "ΔΗΜΟΣ ΔΟΚΙΜΗΣ") == 1.0


def test_completely_different_strings_score_low():
    assert normalized_similarity("ΔΗΜΟΣ ΔΟΚΙΜΗΣ", "ΥΠΟΥΡΓΕΙΟ ΟΙΚΟΝΟΜΙΚΩΝ") < 0.4


def test_missing_side_scores_zero():
    assert normalized_similarity(None, "ΔΗΜΟΣ ΔΟΚΙΜΗΣ") == 0.0
    assert normalized_similarity("ΔΗΜΟΣ ΔΟΚΙΜΗΣ", None) == 0.0
    assert normalized_similarity("", "ΔΗΜΟΣ ΔΟΚΙΜΗΣ") == 0.0
