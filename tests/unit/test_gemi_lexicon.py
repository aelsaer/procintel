from services.ingestion.connectors.gemi.lexicon import (
    STABLE_STATUSES,
    normalize_company_status,
    normalize_legal_form_code,
)


def test_normalizes_common_legal_form_spellings():
    assert normalize_legal_form_code("Ιδιωτική Κεφαλαιουχική Εταιρεία") == "IKE"
    assert normalize_legal_form_code("Α.Ε.") == "AE"
    assert normalize_legal_form_code("ε.π.ε.") == "EPE"


def test_normalizes_common_status_spellings():
    assert normalize_company_status("ΕΝΕΡΓΗ") == "ACTIVE"
    assert normalize_company_status("εν ενεργεια") == "ACTIVE"
    assert normalize_company_status("Υπό Εκκαθάριση") == "IN_LIQUIDATION"
    assert normalize_company_status("Διαγραμμένη") == "DEREGISTERED"


def test_unrecognized_label_passes_through_normalized_casing_not_dropped():
    assert normalize_legal_form_code("ΚΑΤΙ ΑΓΝΩΣΤΟ") == "ΚΑΤΙ ΑΓΝΩΣΤΟ"
    assert normalize_company_status(" κατι αγνωστο ") == "ΚΑΤΙ ΑΓΝΩΣΤΟ"


def test_none_input_returns_none():
    assert normalize_legal_form_code(None) is None
    assert normalize_company_status(None) is None


def test_stable_statuses_is_canonical_only():
    assert STABLE_STATUSES == frozenset({"ACTIVE"})
