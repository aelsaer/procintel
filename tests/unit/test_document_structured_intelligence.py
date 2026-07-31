from services.documents.intelligence import compare_document_terms, extract_compliance_fields


def test_extracts_normalized_compliance_fields_with_page_evidence():
    fields = extract_compliance_fields(
        [
            {
                "document_id": "doc-1",
                "page_number": 3,
                "text": (
                    "Η προθεσμία υποβολής προσφορών είναι 30/09/2026 ώρα 14:30. "
                    "Ο προϋπολογισμός ανέρχεται σε 125.000,00 ευρώ. "
                    "Απαιτείται πιστοποιητικό ISO 27001."
                ),
            }
        ]
    )
    by_name = {field.field_name: field for field in fields}
    assert by_name["submission_deadline"].value["normalized"] == "2026-09-30T14:30:00"
    assert by_name["estimated_value"].value["normalized"] == "125000.00"
    assert by_name["certificate_requirement"].page_number == 3


def test_compares_added_removed_and_changed_terms():
    result = compare_document_terms(
        [{"page_number": 1, "text": "Η διάρκεια είναι δώδεκα μήνες. Απαιτείται ISO 9001."}],
        [{"page_number": 1, "text": "Η διάρκεια είναι δεκαοκτώ μήνες. Απαιτείται ISO 27001. Νέα εγγύηση 2%."}],
    )
    assert result["counts"]["CHANGED"] >= 1
    assert result["counts"]["ADDED"] >= 1
