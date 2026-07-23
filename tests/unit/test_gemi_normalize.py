import json
from pathlib import Path

from services.ingestion.connectors.gemi.normalize import normalize_company_record

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gemi" / "company_sample.json"


def test_normalizes_company_record_from_fixture():
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    normalized = normalize_company_record(raw, afm="090000045")

    assert normalized.afm_normalized == "090000045"
    assert normalized.gemi_number == "123456789000"
    assert normalized.official_name == raw["coNameEl"]
    assert normalized.trade_name == raw["coTitlesEl"][0]
    assert normalized.legal_form == raw["legalType"]["descr"]
    assert normalized.legal_form_code == "IKE"
    assert normalized.company_status == "ACTIVE"
    assert normalized.gemi_office == "ΓΕΜΗ ΑΘΗΝΩΝ"
    assert normalized.gemi_registration_date.isoformat() == "2015-06-01"
    assert normalized.kad_codes == ["81210000"]
    assert normalized.municipality == "ΑΘΗΝΑΙΩΝ"
    assert normalized.region == "ΑΤΤΙΚΗΣ"


def test_normalize_strips_afm_formatting():
    normalized = normalize_company_record({}, afm="090-000-045")
    assert normalized.afm_normalized == "090000045"


def test_normalize_handles_missing_optional_fields():
    normalized = normalize_company_record({}, afm="090000045")
    assert normalized.official_name is None
    assert normalized.kad_codes == []
