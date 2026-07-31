from services.intelligence.decision_makers import classify_decision_role, public_contact_fields


def test_classifies_procurement_before_generic_manager_role():
    assert classify_decision_role("Προϊστάμενος Τμήματος Προμηθειών", None) == "PROCUREMENT"


def test_classifies_public_sector_roles_in_greek_and_english():
    assert classify_decision_role("Διευθυντής Οικονομικών", None) == "FINANCE"
    assert classify_decision_role("Head of Technical Services", None) == "TECHNICAL"
    assert classify_decision_role(None, "Άγνωστη μονάδα") == "STAKEHOLDER"


def test_accepts_only_valid_official_record_contact_shapes():
    contact = public_contact_fields(
        {
            "emailAddress": "procurement@example.gov.gr",
            "telephoneNumber": "+30 210 123 4567",
            "profileUrl": "https://diavgeia.gov.gr/f/example",
        }
    )
    assert contact.email == "procurement@example.gov.gr"
    assert contact.phone == "+30 210 123 4567"
    assert contact.profile_url == "https://diavgeia.gov.gr/f/example"

    invalid = public_contact_fields(
        {"email": "not-an-email", "phone": "123", "profileUrl": "javascript:alert(1)"}
    )
    assert invalid.email is None
    assert invalid.phone is None
    assert invalid.profile_url is None
