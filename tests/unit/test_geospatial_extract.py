from services.geospatial.extract import (
    AdminUnit,
    extract_location_candidates,
    has_explicit_foreign_performance,
)


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
    assert any(
        "gazetteer" in source_path
        for candidate in candidates
        for source_path in candidate.source_paths
    )


def test_nationwide_place_is_not_misrepresented_as_a_point():
    candidates = extract_location_candidates(
        {
            "nutsCity": "ΕΛΛΑΔΑ",
            "objectDetailsList": [{"city": "Σε όλη την ελληνική επικράτεια"}],
        }
    )

    assert candidates == []


def test_nuts_code_alone_resolves_to_loaded_regional_unit():
    units = [
        AdminUnit(
            "REGIONAL_UNIT",
            "EL303",
            "Κεντρικός Τομέας Αθηνών",
            "EL303",
            37.98,
            23.73,
        )
    ]

    candidates = extract_location_candidates(
        {"nutsCode": {"key": "EL303"}},
        admin_units=units,
    )

    assert len(candidates) == 1
    assert candidates[0].place_text == "Κεντρικός Τομέας Αθηνών"
    assert candidates[0].nuts_codes == ("EL303",)
    assert candidates[0].granularity_hint == "REGIONAL_UNIT"
    assert candidates[0].extraction_method == "NUTS_CODE"


def test_legacy_gr_nuts_prefix_is_normalized_to_current_el_code():
    units = [
        AdminUnit("REGION", "EL30", "Αττική", "EL30", 37.9, 23.7),
    ]

    candidates = extract_location_candidates(
        {"nutsCode": {"key": "GR30"}},
        admin_units=units,
    )

    assert candidates[0].nuts_codes == ("EL30",)


def test_object_details_and_postal_locality_are_supported():
    candidates = extract_location_candidates(
        {
            "objectDetails": [
                {
                    "shortDescription": "Στοιχεία υπηρεσίας\nΤαχ. Κώδ.: 84100 Σύρος",
                }
            ]
        }
    )

    locality = next(
        candidate
        for candidate in candidates
        if candidate.extraction_method == "POSTAL_LOCALITY_PATTERN"
    )
    assert locality.place_text == "Σύρος"
    assert locality.postal_code == "84100"
    assert locality.source_paths == (
        "$.objectDetails[0].shortDescription:postal-locality",
    )


def test_foreign_ted_place_is_classified_without_greek_geocoding():
    raw = {"place-of-performance": ["HR031", "HRV"]}

    assert has_explicit_foreign_performance(raw) is True
    assert extract_location_candidates(raw) == []


def test_mixed_greek_and_foreign_ted_place_is_not_classified_as_foreign():
    raw = {"place-of-performance": ["EL303", "HR031"]}

    assert has_explicit_foreign_performance(raw) is False
