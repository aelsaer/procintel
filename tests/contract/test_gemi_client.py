"""ΓΕΜΗ Open Data v1 wire-contract tests with mocked HTTP."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from packages.source_clients.retry import TransientServerError
from services.ingestion.connectors.gemi.client import (
    CompanyNotFoundError,
    GemiAuthenticationError,
    GemiClient,
    GemiInvalidResponseError,
)
from services.ingestion.connectors.gemi.config import GemiConnectorConfig
from services.ingestion.connectors.gemi.provider import CompanySearchQuery, GemiCompanyRegistryProvider

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gemi" / "company_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
SEARCH_BODY = {"searchMetadata": {"total": 1}, "searchResults": [SAMPLE_BODY]}
DOCUMENTS_BODY = {"decision": [], "publication": [{"title": "Ανακοίνωση σύστασης"}]}

BASE_URL = "https://gemi.example.test"
AFM = "090000045"
GEMI_NUMBER = "123456789000"


def _config(**overrides) -> GemiConnectorConfig:
    return GemiConnectorConfig(
        base_url=BASE_URL,
        api_key="test-key",
        rate_limit_per_minute=6000,
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        **overrides,
    )


@respx.mock
async def test_find_by_vat_uses_official_search_envelope_and_api_key_header():
    route = respx.get(
        f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
    ).mock(return_value=httpx.Response(200, json=SEARCH_BODY))

    client = GemiClient(_config())
    try:
        response = await client.find_by_vat(AFM)
    finally:
        await client.aclose()

    assert response.body["afm"] == AFM
    assert route.calls[0].request.headers["api_key"] == "test-key"


@respx.mock
async def test_find_by_gemi_uses_company_resource():
    respx.get(f"{BASE_URL}/companies/{GEMI_NUMBER}").mock(
        return_value=httpx.Response(200, json=SAMPLE_BODY)
    )
    client = GemiClient(_config())
    try:
        response = await client.find_by_gemi_number(GEMI_NUMBER)
    finally:
        await client.aclose()
    assert response.body["arGemi"] == 123456789000


@respx.mock
async def test_get_company_documents_uses_published_documents_resource():
    respx.get(f"{BASE_URL}/companies/{GEMI_NUMBER}/documents").mock(
        return_value=httpx.Response(200, json=DOCUMENTS_BODY)
    )
    client = GemiClient(_config())
    try:
        response = await client.get_company_documents(GEMI_NUMBER)
    finally:
        await client.aclose()
    assert response.body["publication"][0]["title"] == "Ανακοίνωση σύστασης"


@respx.mock
async def test_empty_search_result_raises_company_not_found():
    respx.get(
        f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
    ).mock(return_value=httpx.Response(200, json={"searchMetadata": {"total": 0}, "searchResults": []}))
    client = GemiClient(_config())
    try:
        with pytest.raises(CompanyNotFoundError):
            await client.find_by_vat(AFM)
    finally:
        await client.aclose()


@respx.mock
async def test_5xx_is_retried_then_raises_on_exhaustion():
    respx.get(
        f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
    ).mock(return_value=httpx.Response(503))
    client = GemiClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.find_by_vat(AFM)
    finally:
        await client.aclose()


@respx.mock
async def test_401_raises_redacted_authentication_error_without_retry():
    route = respx.get(
        f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
    ).mock(return_value=httpx.Response(401, json={"message": "unauthorized"}))
    client = GemiClient(_config())
    try:
        with pytest.raises(GemiAuthenticationError, match="HTTP 401") as error:
            await client.find_by_vat(AFM)
    finally:
        await client.aclose()

    assert route.call_count == 1
    assert "test-key" not in str(error.value)


@respx.mock
async def test_429_honors_retry_and_then_returns_json():
    route = respx.get(
        f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
    ).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=SEARCH_BODY),
        ]
    )
    client = GemiClient(_config(max_retry_attempts=2))
    try:
        response = await client.find_by_vat(AFM)
    finally:
        await client.aclose()

    assert response.body["afm"] == AFM
    assert route.call_count == 2


@respx.mock
async def test_successful_non_json_response_is_rejected():
    respx.get(
        f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
    ).mock(return_value=httpx.Response(200, text="not-json"))
    client = GemiClient(_config())
    try:
        with pytest.raises(GemiInvalidResponseError, match="non-JSON"):
            await client.find_by_vat(AFM)
    finally:
        await client.aclose()


@respx.mock
async def test_provider_enriches_company_with_public_documents():
    respx.get(
        f"{BASE_URL}/companies", params={"afm": AFM, "resultsSize": "1"}
    ).mock(return_value=httpx.Response(200, json=SEARCH_BODY))
    docs_route = respx.get(f"{BASE_URL}/companies/{GEMI_NUMBER}/documents").mock(
        return_value=httpx.Response(200, json=DOCUMENTS_BODY)
    )
    client = GemiClient(_config())
    provider = GemiCompanyRegistryProvider(client)
    try:
        result = await provider.find_by_vat("GR", AFM)
    finally:
        await client.aclose()

    assert result.company is not None
    assert result.company.gemi_number == GEMI_NUMBER
    assert result.raw_response is not None
    assert result.raw_response.body["publicDocuments"] == DOCUMENTS_BODY
    assert docs_route.call_count == 1


@respx.mock
async def test_provider_returns_none_for_foreign_country_without_request():
    client = GemiClient(_config())
    provider = GemiCompanyRegistryProvider(client)
    try:
        result = await provider.find_by_vat("DE", "123456789")
    finally:
        await client.aclose()
    assert result.company is None
    assert result.raw_response is None


@respx.mock
async def test_provider_search_maps_query_to_official_parameter_names():
    route = respx.get(
        f"{BASE_URL}/companies",
        params={"name": "ΑΛΦΑ", "activities": "81210000", "prefectures": "ΑΤΤΙΚΗΣ"},
    ).mock(return_value=httpx.Response(200, json=SEARCH_BODY))
    client = GemiClient(_config())
    provider = GemiCompanyRegistryProvider(client)
    try:
        results = await provider.search(
            CompanySearchQuery(name="ΑΛΦΑ", kad="81210000", prefecture="ΑΤΤΙΚΗΣ")
        )
    finally:
        await client.aclose()

    assert route.call_count == 1
    assert results[0].afm_normalized == AFM
    assert results[0].gemi_number == GEMI_NUMBER
