from datetime import date
from decimal import Decimal

from services.search_index.document import ActForIndexing, build_act_document


def test_build_act_document_converts_decimals_and_dates():
    act = ActForIndexing(
        id="act-1",
        process_id="proc-1",
        adam="25SYMV000000001",
        ada_list=["7Α1Η465ΦΘΘ-ΘΙΚ"],
        title="Καθαρισμός κτιρίων",
        normalized_title="ΚΑΘΑΡΙΣΜΟΣ ΚΤΙΡΙΩΝ",
        act_type="CONTRACT",
        status="ACTIVE",
        procedure_type="OPEN",
        amount_net=Decimal("1000.00"),
        amount_gross=Decimal("1240.00"),
        currency="EUR",
        cpv_codes=["90910000"],
        nuts_codes=["EL301"],
        buyer_id="buyer-1",
        buyer_name="Δήμος Αθηναίων",
        supplier_ids=["sup-1"],
        supplier_names=["Καθαριότητα ΑΕ"],
        submission_date=date(2025, 1, 10),
        decision_date=date(2025, 2, 1),
    )
    doc = build_act_document(act)

    assert doc["id"] == "act-1"
    assert doc["amount_net"] == 1000.0
    assert isinstance(doc["amount_net"], float)
    assert doc["amount_gross"] == 1240.0
    assert doc["submission_date"] == "2025-01-10"
    assert doc["decision_date"] == "2025-02-01"
    assert doc["cpv_codes"] == ["90910000"]
    assert doc["ada_list"] == ["7Α1Η465ΦΘΘ-ΘΙΚ"]


def test_build_act_document_handles_missing_optional_fields():
    act = ActForIndexing(
        id="act-2",
        process_id=None,
        adam=None,
        ada_list=[],
        title=None,
        normalized_title=None,
        act_type="REQUEST",
        status=None,
        procedure_type=None,
        amount_net=None,
        amount_gross=None,
        currency=None,
    )
    doc = build_act_document(act)

    assert doc["amount_net"] is None
    assert doc["submission_date"] is None
    assert doc["cpv_codes"] == []
    assert doc["buyer_id"] is None
