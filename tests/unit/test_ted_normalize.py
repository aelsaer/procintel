import json
from decimal import Decimal
from pathlib import Path

from services.ingestion.connectors.ted.normalize import normalize_ted_notice

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "ted"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_normalizes_notice_with_explicit_eforms_version():
    raw = _load("notice_sample.json")
    normalized = normalize_ted_notice(raw, ted_notice_id="2025-TED-000123")

    assert normalized.ted_notice_id == "2025-TED-000123"
    assert normalized.eforms_version is None
    assert normalized.parse_confidence == 0.85
    assert normalized.buyer.vat == "094259216"
    assert normalized.buyer.country_code == "GR"
    assert normalized.supplier.vat == "090000045"
    assert normalized.cpv_codes == ["90911200"]
    assert normalized.estimated_value == Decimal("100000.00")
    assert normalized.awarded_value == Decimal("124000.00")
    assert normalized.publication_date.isoformat() == "2025-01-15"


def test_normalizes_foreign_supplier_notice():
    raw = _load("notice_foreign_supplier_sample.json")
    normalized = normalize_ted_notice(raw, ted_notice_id="2025-TED-000456")

    assert normalized.supplier.country_code == "DE"
    assert normalized.supplier.vat == "DE123456789"


def test_normalizes_portuguese_country_nuts_and_earliest_lot_deadline():
    normalized = normalize_ted_notice(
        {
            "publication-number": "123-2026",
            "buyer-country": ["PRT"],
            "buyer-name": {"eng": ["Lisbon Municipality"]},
            "place-of-performance": ["PT170", "Lisboa"],
            "deadline-receipt-tender-date-lot": ["2026-09-20", "2026-09-15"],
            "deadline-receipt-tender-time-lot": ["17:00:00", "12:30:00"],
        },
        ted_notice_id="PT-2026-123",
    )

    assert normalized.country_code == "PT"
    assert normalized.buyer.country_code == "PT"
    assert normalized.nuts_codes == ["PT170"]
    assert normalized.submission_deadline.isoformat() == "2026-09-15T12:30:00+00:00"


def test_legacy_form_marker_yields_no_eforms_version_high_confidence():
    normalized = normalize_ted_notice({"legacyFormType": "F03"}, ted_notice_id="LEGACY-1")
    assert normalized.eforms_version is None
    assert normalized.parse_confidence == 1.0


def test_unrecognized_shape_gets_low_confidence_not_a_guessed_version():
    normalized = normalize_ted_notice({"somethingElse": True}, ted_notice_id="UNKNOWN-1")
    assert normalized.eforms_version is None
    assert normalized.parse_confidence == 0.3


def test_plausible_but_unmarked_shape_gets_medium_confidence():
    normalized = normalize_ted_notice({"publicationNumber": "1-2025"}, ted_notice_id="MEDIUM-1")
    assert normalized.eforms_version is None
    assert normalized.parse_confidence == 0.6
