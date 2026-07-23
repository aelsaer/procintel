"""Connector-level tests against mocked HTTP (respx) — no live data.gov.gr
access required or attempted."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from packages.source_clients.retry import TransientServerError
from services.ingestion.connectors.ckan.client import CkanActionError, CkanClient
from services.ingestion.connectors.ckan.config import CkanConnectorConfig

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "ckan" / "package_show_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

BASE_URL = "https://data.gov.gr.example.test"
DATASET_ID = "plithysmos-dimon-2021"


def _config(**overrides) -> CkanConnectorConfig:
    return CkanConnectorConfig(
        base_url=BASE_URL,
        rate_limit_per_minute=6000,
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        **overrides,
    )


@respx.mock
async def test_package_show_parses_response():
    respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID}).mock(
        return_value=httpx.Response(200, json=SAMPLE_BODY)
    )

    client = CkanClient(_config())
    try:
        response = await client.package_show(DATASET_ID)
    finally:
        await client.aclose()

    assert response.catalog_dataset_id == DATASET_ID
    assert response.title == SAMPLE_BODY["result"]["title"]
    assert response.publisher == "ΕΛΣΤΑΤ"
    assert response.license_code == "cc-by-4.0"
    assert response.resources == SAMPLE_BODY["result"]["resources"]


@respx.mock
async def test_package_search_parses_results():
    respx.get(f"{BASE_URL}/api/3/action/package_search", params={"q": "population", "rows": 100}).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"count": 1, "results": [{"id": "x"}]}})
    )

    client = CkanClient(_config())
    try:
        response = await client.package_search("population")
    finally:
        await client.aclose()

    assert response.count == 1
    assert response.results == [{"id": "x"}]


@respx.mock
async def test_success_false_raises_ckan_action_error():
    respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID}).mock(
        return_value=httpx.Response(200, json={"success": False, "error": {"message": "Not found"}})
    )

    client = CkanClient(_config())
    try:
        with pytest.raises(CkanActionError):
            await client.package_show(DATASET_ID)
    finally:
        await client.aclose()


@respx.mock
async def test_fetch_resource_bytes_downloads_raw_content():
    resource_url = "https://files.example.test/population.csv"
    respx.get(resource_url).mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))

    client = CkanClient(_config())
    try:
        response = await client.fetch_resource_bytes(resource_url)
    finally:
        await client.aclose()

    assert response.content == b"a,b\n1,2\n"
    assert response.http_status == 200


@respx.mock
async def test_5xx_is_retried_then_raises_on_exhaustion():
    respx.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID}).mock(
        return_value=httpx.Response(503)
    )

    client = CkanClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.package_show(DATASET_ID)
    finally:
        await client.aclose()
