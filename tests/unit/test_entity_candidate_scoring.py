from services.entity_resolution.candidates import score_candidate


def test_multifield_candidate_uses_contacts_address_time_and_reliability():
    score, breakdown = score_candidate(
        {
            "name_similarity": 0.92,
            "address_similarity": 0.88,
            "municipality_a": "Αθήνα",
            "municipality_b": "αθήνα",
            "postal_a": "105 58",
            "postal_b": "10558",
            "domains_a": ["example.gr"],
            "domains_b": ["example.gr"],
            "phones_a": ["302101234567"],
            "phones_b": ["302101234567"],
            "emails_a": ["info@example.gr"],
            "emails_b": ["sales@example.gr"],
            "temporal_compatibility": True,
            "source_reliability": 1,
        }
    )
    assert score >= 0.85
    assert breakdown["domain_match"] is True
    assert breakdown["phone_match"] is True
    assert breakdown["postal_code_match"] is True
    assert breakdown["suggested_action"] == "REVIEW_HIGH"


def test_conflicting_valid_afms_cap_candidate_score():
    score, breakdown = score_candidate(
        {
            "name_similarity": 1,
            "address_similarity": 1,
            "municipality_a": "Αθήνα",
            "municipality_b": "Αθήνα",
            "postal_a": "10558",
            "postal_b": "10558",
            "domains_a": ["example.gr"],
            "domains_b": ["example.gr"],
            "phones_a": ["302101234567"],
            "phones_b": ["302101234567"],
            "emails_a": ["info@example.gr"],
            "emails_b": ["info@example.gr"],
            "afms_a": ["094259216"],
            "afms_b": ["123456789"],
            "source_reliability": 1,
        }
    )
    assert score == 0.69
    assert breakdown["identifier_conflict"] is True
    assert breakdown["suggested_action"] == "REJECT_CONFLICT"
