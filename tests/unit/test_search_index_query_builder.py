from services.search_index.search import build_query_body


def test_query_body_includes_multi_match_over_title_buyer_supplier():
    body = build_query_body(query="καθαρισμός", cpv_prefix=None, nuts_code=None, offset=0, limit=20)
    must = body["query"]["bool"]["must"]
    assert must[0]["multi_match"]["query"] == "καθαρισμός"
    assert must[0]["multi_match"]["fields"] == ["title^3", "buyer_name^2", "supplier_names"]
    assert must[0]["multi_match"]["operator"] == "and"
    assert must[0]["multi_match"]["fuzziness"] == 0
    assert body["from"] == 0
    assert body["size"] == 20
    assert body["query"]["bool"]["filter"] == []


def test_query_body_adds_cpv_prefix_wildcard_filter():
    body = build_query_body(query="q", cpv_prefix="9091", nuts_code=None, offset=0, limit=20)
    filters = body["query"]["bool"]["filter"]
    assert {"wildcard": {"cpv_codes": "9091*"}} in filters


def test_query_body_adds_nuts_code_term_filter():
    body = build_query_body(query="q", cpv_prefix=None, nuts_code="EL301", offset=0, limit=20)
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"nuts_codes": "EL301"}} in filters


def test_query_body_respects_offset_and_limit():
    body = build_query_body(query="q", cpv_prefix=None, nuts_code=None, offset=40, limit=10)
    assert body["from"] == 40
    assert body["size"] == 10
