"""Regression test for a real production failure: the live ΚΗΜΔΗΣ API
returns `procedureType` (and `procedureCategory`) as a coded
`{"key": ..., "value": ...}` object, not a plain string — surfaced as a
Pydantic `NormalizedAct` validation error during a real June-2026 backfill
(`procedure_type: Input should be a valid string [type=string_type,
input_value={'key': '6', 'value': 'Απευθείας ανάθεση'}, ...]`).

`_key_value`/`_key_value_str` already existed for this exact shape
(`organization`, `nutsCode`, `cpvItems` all used it) — `procedureType`/
`procedureCategory` just weren't wired through it yet.
"""

from services.ingestion.connectors.khmdhs.normalize import (
    _key_value,
    _key_value_str,
    normalize_khmdhs_record,
)

_BASE_RAW = {
    "referenceNumber": "26SYMV000000099",
    "title": "Test contract",
    "organizationVatNumber": "090000045",
}


def test_key_value_prefers_the_requested_key():
    assert _key_value({"key": "6", "value": "Απευθείας ανάθεση"}, prefer="value") == "Απευθείας ανάθεση"
    assert _key_value({"key": "6", "value": "Απευθείας ανάθεση"}, prefer="key") == "6"


def test_key_value_falls_back_to_the_other_key_when_preferred_one_is_missing():
    assert _key_value({"value": "Απευθείας ανάθεση"}, prefer="key") == "Απευθείας ανάθεση"


def test_key_value_passes_through_a_plain_string_unchanged():
    assert _key_value("Απευθείας ανάθεση", prefer="value") == "Απευθείας ανάθεση"


def test_key_value_returns_none_for_an_empty_or_unrecognized_dict():
    assert _key_value({}, prefer="value") is None
    assert _key_value({"unrelated": "x", "other": "y"}, prefer="value") is None


def test_key_value_str_coerces_to_string_and_passes_through_none():
    assert _key_value_str({"key": "6", "value": "Απευθείας ανάθεση"}, prefer="value") == "Απευθείας ανάθεση"
    assert _key_value_str(None, prefer="value") is None


def test_procedure_type_as_coded_object_no_longer_raises_and_extracts_the_text():
    raw = {**_BASE_RAW, "procedureType": {"key": "6", "value": "Απευθείας ανάθεση"}}
    normalized = normalize_khmdhs_record(raw, resource="contract")
    assert normalized.procedure_type == "Απευθείας ανάθεση"


def test_procedure_type_as_plain_string_still_works():
    raw = {**_BASE_RAW, "procedureType": "Ανοικτή διαδικασία"}
    normalized = normalize_khmdhs_record(raw, resource="contract")
    assert normalized.procedure_type == "Ανοικτή διαδικασία"


def test_procedure_category_fallback_also_handles_coded_object():
    raw = {**_BASE_RAW, "procedureCategory": {"key": "1", "value": "Ανοικτή διαδικασία"}}
    normalized = normalize_khmdhs_record(raw, resource="contract")
    assert normalized.procedure_type == "Ανοικτή διαδικασία"


def test_procedure_type_missing_entirely_is_none():
    normalized = normalize_khmdhs_record(dict(_BASE_RAW), resource="contract")
    assert normalized.procedure_type is None
