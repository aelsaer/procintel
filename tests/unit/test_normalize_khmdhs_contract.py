import json
from decimal import Decimal
from pathlib import Path

import pytest

from services.ingestion.connectors.khmdhs.normalize import normalize_contract_record

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json"


@pytest.fixture(scope="module")
def sample_records() -> list[dict]:
    body = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return body["data"]


def test_normalizes_first_record_with_lowercase_ada_field(sample_records):
    normalized = normalize_contract_record(sample_records[0])

    assert normalized.source_native_id == "25SYMV012345678"
    assert normalized.adam_normalized == "25SYMV012345678"
    assert normalized.amount_net == Decimal("100000.00")
    assert normalized.vat_amount == Decimal("24000.00")
    assert normalized.amount_gross == Decimal("124000.00")
    assert normalized.cpv_codes == ["90911200", "90910000"]
    assert normalized.nuts_codes == ["EL301"]
    assert "7Α1Η465ΦΘΘ-ΘΙΚ" in normalized.related_ada
    assert "6Ω0Ζ465ΦΘΘ-ΔΕΖ" in normalized.related_ada


def test_normalizes_second_record_with_uppercase_ada_field_and_nuts_list(sample_records):
    # this record uses contractRelatedADA (uppercase) and nutsCodes (list) —
    # the casing-drift / list-vs-scalar handling under test
    normalized = normalize_contract_record(sample_records[1])

    assert "9Γ3Κ465ΦΘΘ-ΞΟΠ" in normalized.related_ada
    assert normalized.nuts_codes == ["EL301", "EL303"]


def test_buyer_and_contractor_afm_are_checksum_validated(sample_records):
    normalized = normalize_contract_record(sample_records[0])

    assert normalized.buyer is not None
    assert normalized.buyer.afm_normalized == "094259216"
    assert normalized.buyer.afm_checksum_valid is True

    assert normalized.contractor is not None
    assert normalized.contractor.afm_normalized == "090000045"
    assert normalized.contractor.afm_checksum_valid is True


def test_invalid_afm_checksum_is_flagged_not_dropped():
    record = {
        "referenceNumber": "25SYMV000000001",
        "organizationVatNumber": "123456789",  # fails checksum
    }
    normalized = normalize_contract_record(record)

    assert normalized.buyer is not None
    assert normalized.buyer.afm_normalized == "123456789"
    assert normalized.buyer.afm_checksum_valid is False


def test_old_khmdhs_notice_keeps_nested_nuts_and_source_org_id():
    record = {
        "referenceNumber": "17PROC001636130",
        "title": "EΡΓΑΣΙΕΣ ΑΠΟΨΙΛΩΣΗΣ",
        "organizationVatNumber": None,
        "organization": {"key": "100025231", "value": "ΕΝΙΑΙΟΣ ΦΟΡΕΑΣ ΚΟΙΝΩΝΙΚΗΣ ΑΣΦΑΛΙΣΗΣ ΕΦΚΑ"},
        "nutsCode": {"key": "EL303", "value": "Κεντρικός Τομέας Αθηνών"},
        "nutsCodes": [{"nutsCode": {"key": "EL3", "value": "ATTIKΗ"}}],
    }

    normalized = normalize_contract_record(record)

    assert normalized.nuts_codes == ["EL303", "EL3"]
    assert normalized.buyer is not None
    assert normalized.buyer.afm_normalized is None
    assert normalized.buyer.source_native_id == "100025231"
    assert normalized.buyer.name == "ΕΝΙΑΙΟΣ ΦΟΡΕΑΣ ΚΟΙΝΩΝΙΚΗΣ ΑΣΦΑΛΙΣΗΣ ΕΦΚΑ"


def test_both_funding_reference_fields_kept_separate_and_unmerged(sample_records):
    first = normalize_contract_record(sample_records[0])
    second = normalize_contract_record(sample_records[1])

    assert first.public_funding_ref_ops == "OPS-0001"
    assert first.espa_fund_program_ref is None

    assert second.public_funding_ref_ops is None
    assert second.espa_fund_program_ref == "ESPA-2025-001"
