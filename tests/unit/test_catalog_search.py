from services.search_index.catalog_search import build_catalog_query


def test_public_catalog_query_requires_all_text_terms() -> None:
    body = build_catalog_query(
        query="γεωγραφικά συστήματα",
        offset=10,
        limit=25,
    )

    assert body["from"] == 10
    assert body["size"] == 25
    text_match = body["query"]["bool"]["should"][0]["multi_match"]
    assert text_match["operator"] == "and"
    assert text_match["fuzziness"] == 0
    assert body["query"]["bool"]["filter"] == []


def test_opportunity_catalog_query_is_tenant_filtered() -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    body = build_catalog_query(
        query="GIS",
        offset=0,
        limit=20,
        tenant_id=tenant_id,
    )

    assert body["query"]["bool"]["filter"] == [
        {"term": {"tenant_id": tenant_id}}
    ]
