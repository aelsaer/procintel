import json
from decimal import Decimal
from pathlib import Path

from services.ingestion.connectors.mef.normalize import normalize_expense_record

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "mef" / "expenses_sample.json"


def test_normalizes_expense_record_from_fixture():
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    normalized = normalize_expense_record(raw["items"][0])

    assert normalized.organization_source_native_id == "MEF-ORG-0001"
    assert normalized.organization_name == "ΥΠΟΥΡΓΕΙΟ ΔΟΚΙΜΗΣ"
    assert normalized.organization_afm == "094259216"
    assert normalized.recipient_afm == "090000045"
    assert normalized.recipient_name == "ΑΛΦΑ ΚΑΘΑΡΙΣΜΟΙ ΙΚΕ"
    assert normalized.amount == Decimal("100000.00")
    assert normalized.vat_amount == Decimal("24000.00")
    assert normalized.expense_date.isoformat() == "2025-01-12"
    assert normalized.related_ada == "7Α1Η465ΦΘΘ-ΘΙΚ"


def test_normalize_handles_missing_related_ada_and_falls_back_field_names():
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    normalized = normalize_expense_record(raw["items"][1])

    assert normalized.related_ada is None
    assert normalized.amount == Decimal("9999.00")
    assert normalized.expense_date.isoformat() == "2020-05-05"


def test_normalize_empty_record_is_all_none():
    normalized = normalize_expense_record({})
    assert normalized.organization_source_native_id is None
    assert normalized.recipient_afm is None
    assert normalized.amount is None
    assert normalized.expense_date is None
    assert normalized.related_ada is None
