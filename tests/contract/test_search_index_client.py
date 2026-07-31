"""OpenSearch REST wrapper against mocked HTTP (respx) — no live cluster
needed. Confirms the bulk NDJSON body shape, index-exists HEAD check, and
error handling for a partially-failed bulk request."""

import json

import httpx
import pytest
import respx

from services.search_index.client import (
    bulk_index,
    create_index,
    delete_all_documents,
    index_exists,
    search,
)
from services.search_index.config import OpenSearchConfig

BASE_URL = "https://opensearch.example.test"


def _config() -> OpenSearchConfig:
    return OpenSearchConfig(base_url=BASE_URL, index_name="procurement_acts")


@respx.mock
async def test_index_exists_true_on_200():
    respx.head(f"{BASE_URL}/procurement_acts").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        assert await index_exists(client, _config()) is True


@respx.mock
async def test_index_exists_false_on_404():
    respx.head(f"{BASE_URL}/procurement_acts").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        assert await index_exists(client, _config()) is False


@respx.mock
async def test_create_index_sends_the_mapping_body():
    route = respx.put(f"{BASE_URL}/procurement_acts").mock(return_value=httpx.Response(200, json={"acknowledged": True}))
    async with httpx.AsyncClient() as client:
        await create_index(client, _config(), {"mappings": {"properties": {}}})
    assert json.loads(route.calls[0].request.content) == {"mappings": {"properties": {}}}


@respx.mock
async def test_create_index_raises_on_failure():
    respx.put(f"{BASE_URL}/procurement_acts").mock(return_value=httpx.Response(400, text="bad mapping"))
    with pytest.raises(RuntimeError):
        async with httpx.AsyncClient() as client:
            await create_index(client, _config(), {})


@respx.mock
async def test_delete_all_documents_uses_match_all_and_refreshes():
    route = respx.post(
        f"{BASE_URL}/procurement_acts/_delete_by_query",
        params={"conflicts": "proceed", "refresh": "true"},
    ).mock(return_value=httpx.Response(200, json={"deleted": 7}))
    async with httpx.AsyncClient() as client:
        deleted = await delete_all_documents(client, _config())
    assert deleted == 7
    assert json.loads(route.calls[0].request.content) == {
        "query": {"match_all": {}}
    }


@respx.mock
async def test_bulk_index_sends_ndjson_with_index_actions():
    route = respx.post(f"{BASE_URL}/_bulk").mock(return_value=httpx.Response(200, json={"errors": False, "items": []}))
    docs = [{"id": "a1", "title": "one"}, {"id": "a2", "title": "two"}]

    async with httpx.AsyncClient() as client:
        await bulk_index(client, _config(), docs)

    body = route.calls[0].request.content.decode()
    lines = [json.loads(line) for line in body.strip().split("\n")]
    assert lines[0] == {"index": {"_index": "procurement_acts", "_id": "a1"}}
    assert lines[1] == {"id": "a1", "title": "one"}
    assert lines[2] == {"index": {"_index": "procurement_acts", "_id": "a2"}}
    assert lines[3] == {"id": "a2", "title": "two"}


@respx.mock
async def test_bulk_index_empty_list_is_a_noop():
    route = respx.post(f"{BASE_URL}/_bulk")
    async with httpx.AsyncClient() as client:
        result = await bulk_index(client, _config(), [])
    assert result == {"items": []}
    assert not route.called


@respx.mock
async def test_bulk_index_raises_on_partial_failure():
    respx.post(f"{BASE_URL}/_bulk").mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": True,
                "items": [
                    {"index": {"status": 400, "error": {"reason": "mapper_parsing_exception"}}},
                    {"index": {"status": 201}},
                ],
            },
        )
    )
    with pytest.raises(RuntimeError):
        async with httpx.AsyncClient() as client:
            await bulk_index(client, _config(), [{"id": "a1"}, {"id": "a2"}])


@respx.mock
async def test_search_posts_the_query_body_and_returns_json():
    respx.post(f"{BASE_URL}/procurement_acts/_search").mock(
        return_value=httpx.Response(200, json={"hits": {"total": {"value": 1}, "hits": []}})
    )
    async with httpx.AsyncClient() as client:
        result = await search(client, _config(), {"query": {"match_all": {}}})
    assert result["hits"]["total"]["value"] == 1
