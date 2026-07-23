from services.geospatial.extract import AdminUnit, extract_location_candidates


def test_structured_execution_city_wins_and_inherits_matching_postcode():
    raw = {
        "title": "Προμήθεια τροφίμων",
        "nutsCity": "ΚΟΖΑΝΗ",
        "nutsPostalCode": "50100",
        "nutsCode": {"key": "EL531", "value": "Γρεβενά, Κοζάνη"},
        "objectDetailsList": [
            {"city": "Βέροια", "shortDescription": "Παράδοση στον Δήμο Βέροιας"},
            {"city": "Βέροια"},
        ],
    }

    candidates = extract_location_candidates(raw)

    assert [candidate.place_text for candidate in candidates[:2]] == ["Βέροια", "ΚΟΖΑΝΗ"]
    assert candidates[0].confidence == 0.97
    assert candidates[0].source_paths == ("$.objectDetailsList[0].city", "$.objectDetailsList[1].city")
    assert candidates[1].postal_code == "50100"
    assert candidates[0].nuts_codes == ("EL531",)


def test_same_structured_city_is_deduped_and_keeps_postcode():
    raw = {
        "nutsCity": "ΚΕΡΚΥΡΑ",
        "nutsPostalCode": "49083",
        "nutsCode": {"key": "EL622", "value": "Κέρκυρα"},
        "objectDetailsList": [{"city": "ΚΕΡΚΥΡΑ"}],
    }

    candidates = extract_location_candidates(raw)

    assert len(candidates) == 1
    assert candidates[0].postal_code == "49083"
    assert candidates[0].confidence == 0.97


def test_explicit_admin_phrases_and_gazetteer_are_extracted_from_documents():
    units = [
        AdminUnit("MUNICIPALITY", "6101", "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ", "EL303", 37.98, 23.73),
    ]
    raw = {"nutsCode": {"key": "EL303"}, "title": "Συντήρηση κτιρίου"}
    documents = ["Το έργο εκτελείται στον Δήμο Αθηναίων, Περιφερειακή Ενότητα Κεντρικού Τομέα Αθηνών."]

    candidates = extract_location_candidates(raw, document_texts=documents, admin_units=units)

    assert any(candidate.granularity_hint == "MUNICIPALITY" for candidate in candidates)
    assert any("ΑΘΗΝΑΙΩΝ" in candidate.place_text.upper() for candidate in candidates)
    assert any(candidate.extraction_method == "GAZETTEER_TEXT_MATCH" for candidate in candidates)


def test_nationwide_place_is_not_misrepresented_as_a_point():
    candidates = extract_location_candidates(
        {
            "nutsCity": "ΕΛΛΑΔΑ",
            "objectDetailsList": [{"city": "Σε όλη την ελληνική επικράτεια"}],
        }
    )

    assert candidates == []
