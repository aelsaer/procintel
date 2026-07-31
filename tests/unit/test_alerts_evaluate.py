from decimal import Decimal

from services.alerts.evaluate import _EVENT_TYPES_BY_ACT_TYPE, material_change_hash, rule_matches


def test_event_type_mapping_covers_expected_act_types():
    assert _EVENT_TYPES_BY_ACT_TYPE["REQUEST"] == ("opportunity.created", "opportunity.updated")
    assert _EVENT_TYPES_BY_ACT_TYPE["NOTICE"] == ("opportunity.created", "opportunity.updated")
    assert _EVENT_TYPES_BY_ACT_TYPE["CONTRACT"] == ("contract.created", "contract.modified")
    assert _EVENT_TYPES_BY_ACT_TYPE["PAYMENT"] == ("payment.detected", "payment.detected")
    # AWARD acts (the `auction` KHMDHS resource) deliberately produce no event —
    # nothing in §30.5's list maps to it
    assert "AWARD" not in _EVENT_TYPES_BY_ACT_TYPE


def test_material_change_hash_is_stable_regardless_of_key_order():
    a = material_change_hash({"amount_net": ["100", "200"], "status": ["OPEN", "CLOSED"]})
    b = material_change_hash({"status": ["OPEN", "CLOSED"], "amount_net": ["100", "200"]})
    assert a == b


def test_material_change_hash_differs_for_different_payloads():
    a = material_change_hash({"amount_net": ["100", "200"]})
    b = material_change_hash({"amount_net": ["100", "300"]})
    assert a != b


def test_rule_matches_empty_filters_matches_everything():
    context = {"cpv_codes": ["72000000"], "buyer_id": "b1", "supplier_id": "s1", "amount_gross": Decimal("1000")}
    assert rule_matches({}, context) is True


def test_rule_matches_cpv_prefix():
    context = {"cpv_codes": ["72000000"], "buyer_id": None, "supplier_id": None, "amount_gross": None}
    assert rule_matches({"cpv_prefix": "72"}, context) is True
    assert rule_matches({"cpv_prefix": "45"}, context) is False


def test_rule_matches_multiple_cpv_prefixes():
    context = {"cpv_codes": ["48800000"], "buyer_id": None, "supplier_id": None, "amount_gross": None}
    assert rule_matches({"cpv_prefixes": ["72", "488"]}, context) is True
    assert rule_matches({"cpv_prefixes": ["45", "909"]}, context) is False


def test_rule_matches_nuts_prefixes():
    context = {
        "cpv_codes": [],
        "nuts_codes": ["EL303"],
        "buyer_id": None,
        "supplier_id": None,
        "amount_gross": None,
    }
    assert rule_matches({"nuts_code": "EL3"}, context) is True
    assert rule_matches({"nuts_codes": ["EL5", "EL6"]}, context) is False


def test_rule_matches_keywords_case_and_accent_insensitive():
    context = {
        "cpv_codes": [],
        "nuts_codes": [],
        "buyer_id": None,
        "supplier_id": None,
        "amount_gross": None,
        "title": "Προμήθεια λογισμικού κυβερνοασφάλειας",
    }
    assert rule_matches({"keywords": ["ΛΟΓΙΣΜΙΚΟΥ", "κυβερνοασφαλειας"]}, context) is True
    assert rule_matches({"keywords": ["καθαρισμός"]}, context) is False


def test_rule_excluded_keyword_overrides_positive_match():
    context = {
        "cpv_codes": ["72200000"],
        "title": "Υπηρεσίες λογισμικού GIS για στρατιωτική χρήση",
    }

    assert rule_matches(
        {
            "keywords": ["λογισμικό"],
            "excluded_keywords": ["στρατιωτική"],
        },
        context,
    ) is False


def test_rule_excluded_cpv_prefix_overrides_broader_positive_prefix():
    context = {
        "cpv_codes": ["72700000"],
        "title": "Υπηρεσίες δικτύων",
    }

    assert rule_matches(
        {
            "cpv_prefixes": ["72"],
            "excluded_cpv_prefixes": ["727"],
        },
        context,
    ) is False


def test_profile_taxonomy_matches_cpv_or_morphological_title_keyword():
    missing_cpv_context = {
        "cpv_codes": [],
        "nuts_codes": [],
        "buyer_id": None,
        "supplier_id": None,
        "amount_gross": Decimal("5000"),
        "title": "Εργασίες αποψίλωσης και καθαρισμού οικοπέδων",
    }
    coded_context = {
        **missing_cpv_context,
        "title": "Συντήρηση χώρων πρασίνου",
        "cpv_codes": ["77312000-0"],
    }
    filters = {
        "cpv_prefixes": ["77312000"],
        "keywords": ["αποψιλώσεις"],
        "taxonomy_match_any": True,
    }

    assert rule_matches(filters, missing_cpv_context) is True
    assert rule_matches(filters, coded_context) is True
    assert rule_matches(filters, {**missing_cpv_context, "title": "Προμήθεια υπολογιστών"}) is False


def test_profile_taxonomy_can_require_cpv_and_lexical_intent():
    filters = {
        "cpv_prefixes": ["72", "38221000"],
        "keywords": ["GIS"],
        "taxonomy_match_mode": "CPV_AND_KEYWORD",
    }
    base = {
        "nuts_codes": [],
        "buyer_id": None,
        "supplier_id": None,
        "amount_gross": Decimal("5000"),
    }

    assert rule_matches({
        **filters,
    }, {**base, "cpv_codes": ["38221000"], "title": "Συντήρηση πλατφόρμας ArcGIS"}) is True
    assert rule_matches(filters, {**base, "cpv_codes": ["72000000"], "title": "Γενικές υπηρεσίες πληροφορικής"}) is False
    assert rule_matches(filters, {**base, "cpv_codes": ["60000000"], "title": "Υπηρεσίες GIS"}) is False
    assert rule_matches(filters, {**base, "cpv_codes": ["72000000"], "title": "Υπηρεσίες LOGISTICS"}) is False


def test_profile_keywords_are_required_while_cpv_remains_supporting_evidence():
    filters = {
        "cpv_prefixes": ["38221000"],
        "keywords": ["GIS"],
        "taxonomy_match_mode": "KEYWORD_REQUIRED",
    }
    base = {
        "nuts_codes": [],
        "buyer_id": None,
        "supplier_id": None,
        "amount_gross": Decimal("5000"),
    }

    assert rule_matches(filters, {**base, "cpv_codes": ["72000000"], "title": "Υπηρεσίες GIS"}) is True
    assert rule_matches(filters, {**base, "cpv_codes": ["38221000"], "title": "Υπηρεσίες LOGISTICS"}) is False


def test_rule_matches_buyer_and_supplier_id():
    context = {"cpv_codes": [], "buyer_id": "buyer-1", "supplier_id": "supplier-1", "amount_gross": None}
    assert rule_matches({"buyer_id": "buyer-1"}, context) is True
    assert rule_matches({"buyer_id": "buyer-2"}, context) is False
    assert rule_matches({"supplier_id": "supplier-1"}, context) is True
    assert rule_matches({"supplier_id": "supplier-2"}, context) is False


def test_rule_matches_amount_range():
    context = {"cpv_codes": [], "buyer_id": None, "supplier_id": None, "amount_gross": Decimal("50000")}
    assert rule_matches({"amount_min": 10000}, context) is True
    assert rule_matches({"amount_min": 60000}, context) is False
    assert rule_matches({"amount_max": 60000}, context) is True
    assert rule_matches({"amount_max": 10000}, context) is False
    assert rule_matches({"amount_min": 10000, "amount_max": 60000}, context) is True


def test_rule_matches_amount_range_with_no_amount_on_context_fails_closed():
    context = {"cpv_codes": [], "buyer_id": None, "supplier_id": None, "amount_gross": None}
    assert rule_matches({"amount_min": 1}, context) is False
    assert rule_matches({"amount_max": 1}, context) is False


def test_rule_matches_municipality_with_accent_and_inflection_tolerance():
    context = {
        "cpv_codes": [],
        "nuts_codes": ["EL30"],
        "location_names": ["Δήμος Αθηναίων"],
        "buyer_id": None,
        "supplier_id": None,
        "amount_gross": None,
    }

    assert rule_matches({"municipality": "Αθήνα"}, context) is True
    assert rule_matches({"municipality": "Ηράκλειο"}, context) is False
