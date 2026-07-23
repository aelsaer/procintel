from services.ingestion.connectors.khmdhs.adamchain import _extract_chain_adams, infer_act_type_from_adam


def test_infer_act_type_from_adam_category_segment():
    assert infer_act_type_from_adam("25REQ012345678") == "REQUEST"
    assert infer_act_type_from_adam("25PROC012345678") == "NOTICE"
    assert infer_act_type_from_adam("25AWRD012345678") == "AWARD"
    assert infer_act_type_from_adam("25SYMV012345678") == "CONTRACT"
    assert infer_act_type_from_adam("25PAY012345678") == "PAYMENT"


def test_infer_act_type_unknown_category_is_unknown():
    assert infer_act_type_from_adam("25XXXX012345678") == "UNKNOWN"


def test_extract_chain_adams_from_flat_string_list():
    body = ["25REQ000000001", "25PROC000000002", "25SYMV000000003"]
    assert _extract_chain_adams(body) == ["25REQ000000001", "25PROC000000002", "25SYMV000000003"]


def test_extract_chain_adams_from_dict_with_reference_number_objects():
    body = {
        "relatedRecords": [
            {"referenceNumber": "25req000000001"},
            {"referenceNumber": "25proc000000002"},
        ]
    }
    # normalized to uppercase regardless of source casing
    assert _extract_chain_adams(body) == ["25REQ000000001", "25PROC000000002"]


def test_extract_chain_adams_tries_alternate_envelope_keys():
    assert _extract_chain_adams({"chain": ["25PAY000000004"]}) == ["25PAY000000004"]
    assert _extract_chain_adams({"data": ["25PAY000000005"]}) == ["25PAY000000005"]
    assert _extract_chain_adams({"unrecognized": []}) == []


def test_extract_chain_adams_from_official_grouped_response():
    body = {
        "requests": ["25REQ009222917"],
        "approvedRequests": ["25REQ009222918"],
        "notices": ["25PROC009223037*"],
        "auctions": ["25AWRD009222920"],
        "contracts": ["25SYMV009222939**"],
        "payments": ["25PAY009223018"],
    }

    assert _extract_chain_adams(body) == [
        "25REQ009222917",
        "25REQ009222918",
        "25PROC009223037",
        "25AWRD009222920",
        "25SYMV009222939",
        "25PAY009223018",
    ]


def test_extract_chain_adams_handles_empty_or_malformed_input():
    assert _extract_chain_adams(None) == []
    assert _extract_chain_adams({}) == []
    assert _extract_chain_adams([{"noReferenceNumber": True}]) == []
