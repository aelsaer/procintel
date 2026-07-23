import json
from decimal import Decimal
from pathlib import Path

from services.ingestion.connectors.anaptyxi.normalize import normalize_project_record

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "anaptyxi" / "project_sample.json"


def test_normalizes_project_record_from_fixture():
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    normalized = normalize_project_record(raw, mis_code="OPS-0001")

    assert normalized.mis_ops_code == "OPS-0001"
    assert normalized.program_code == "ΕΠΑνΕΚ"
    assert normalized.title == raw["title"]
    assert normalized.beneficiary_afm == "094259216"
    assert normalized.beneficiary_name == "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"
    assert normalized.budget == Decimal("500000.00")
    assert normalized.contracted_amount == Decimal("300000.00")
    assert normalized.paid_amount == Decimal("100000.00")
    assert normalized.start_date.isoformat() == "2023-01-01"
    assert normalized.end_date.isoformat() == "2026-12-31"
    assert normalized.status == "ΣΕ ΕΞΕΛΙΞΗ"


def test_normalize_falls_back_to_synthetic_title_when_missing():
    normalized = normalize_project_record({}, mis_code="MIS-999")
    assert normalized.mis_ops_code == "MIS-999"
    assert "MIS-999" in normalized.title
    assert normalized.beneficiary_afm is None
