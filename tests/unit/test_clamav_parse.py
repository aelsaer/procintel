from services.documents.clamav import parse_clamd_response


def test_ok_response_is_clean():
    result = parse_clamd_response(b"stream: OK\x00")
    assert result.is_clean is True
    assert result.signature is None


def test_found_response_extracts_signature_name():
    result = parse_clamd_response(b"stream: Eicar-Test-Signature FOUND\x00")
    assert result.is_clean is False
    assert result.signature == "Eicar-Test-Signature"


def test_error_response_is_treated_as_not_clean_fail_closed():
    result = parse_clamd_response(b"stream: Access denied. ERROR\x00")
    assert result.is_clean is False
    assert result.signature is not None


def test_unrecognized_response_is_treated_as_not_clean_fail_closed():
    result = parse_clamd_response(b"garbage that isn't a real clamd reply")
    assert result.is_clean is False


def test_empty_response_is_treated_as_not_clean_fail_closed():
    result = parse_clamd_response(b"")
    assert result.is_clean is False
