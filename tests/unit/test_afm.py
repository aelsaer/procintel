from services.ingestion.connectors.khmdhs.afm import valid_greek_afm


def test_valid_afm_passes_checksum():
    assert valid_greek_afm("094259216") is True
    assert valid_greek_afm("090000045") is True


def test_invalid_afm_fails_checksum():
    assert valid_greek_afm("123456789") is False
    assert valid_greek_afm("999999999") is False


def test_wrong_length_is_invalid():
    assert valid_greek_afm("1234567") is False
    assert valid_greek_afm("1234567890") is False


def test_non_digit_characters_are_stripped_before_validation():
    # formatting characters must not change the result either way
    assert valid_greek_afm("094-259-216") is True
    assert valid_greek_afm("123-456-789") is False
