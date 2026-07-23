import json
from pathlib import Path

from services.ingestion.connectors.diavgeia.normalize import normalize_ada, normalize_decision_record

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "diavgeia" / "decision_sample.json"


def test_normalize_ada_uppercases_and_strips():
    assert normalize_ada("  7α1η465φθθ-θικ ") == "7Α1Η465ΦΘΘ-ΘΙΚ"


def test_normalizes_decision_record_from_fixture():
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    normalized = normalize_decision_record(raw, ada="7Α1Η465ΦΘΘ-ΘΙΚ")

    assert normalized.ada_normalized == "7Α1Η465ΦΘΘ-ΘΙΚ"
    assert normalized.subject == raw["subject"]
    assert normalized.decision_type == raw["type"]
    assert normalized.decision_date.isoformat() == "2025-01-09"
    assert normalized.protocol_number == "12345/2025"
    assert normalized.issuing_authority_name == "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"
    assert normalized.organizational_unit_name == "ΤΜΗΜΑ ΠΡΟΜΗΘΕΙΩΝ"
    assert normalized.document_url == raw["documentUrl"]
    assert normalized.signer_names == ["Ιωάννης Παπαδόπουλος", "Μαρία Γεωργίου"]


def test_normalize_handles_missing_optional_fields():
    normalized = normalize_decision_record({}, ada="X1Y2Z3-ABC")
    assert normalized.ada_normalized == "X1Y2Z3-ABC"
    assert normalized.subject is None
    assert normalized.decision_date is None
    assert normalized.signer_names == []


def test_normalize_signers_accepts_plain_string_list():
    normalized = normalize_decision_record({"signers": ["Νίκος Νικολάου", "  ", ""]}, ada="X1Y2Z3-ABC")
    assert normalized.signer_names == ["Νίκος Νικολάου"]
