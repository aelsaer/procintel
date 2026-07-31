"""Connector-level tests against mocked HTTP (respx) — no live
ΑΝΑΠΤΥΞΗ.gov.gr access required or attempted."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from packages.source_clients.retry import TransientServerError
from services.ingestion.connectors.anaptyxi.client import AnaptyxiClient, ProjectNotFoundError
from services.ingestion.connectors.anaptyxi.config import AnaptyxiConnectorConfig

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "anaptyxi" / "project_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

BASE_URL = "https://anaptyxi.example.test"
MIS = "OPS-0001"


def _config(**overrides) -> AnaptyxiConnectorConfig:
    return AnaptyxiConnectorConfig(
        base_url=BASE_URL,
        rate_limit_per_minute=6000,
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        **overrides,
    )


@respx.mock
async def test_find_project_by_mis_parses_response():
    respx.get(
        f"{BASE_URL}/GetData.ashx",
        params={
            "queryType": "projectDetails",
            "queryArgument": MIS,
            "projectDetails": "all",
            "outputFormat": "json",
        },
    ).mock(
        return_value=httpx.Response(200, json=SAMPLE_BODY)
    )

    client = AnaptyxiClient(_config())
    try:
        response = await client.find_project_by_mis(MIS)
    finally:
        await client.aclose()

    assert response.mis_code == MIS
    assert response.body["title"] == SAMPLE_BODY["title"]


@respx.mock
async def test_404_raises_project_not_found():
    respx.get(f"{BASE_URL}/GetData.ashx").mock(return_value=httpx.Response(404))

    client = AnaptyxiClient(_config())
    try:
        with pytest.raises(ProjectNotFoundError):
            await client.find_project_by_mis(MIS)
    finally:
        await client.aclose()


@respx.mock
async def test_200_non_json_project_body_means_not_found():
    respx.get(f"{BASE_URL}/GetData.ashx").mock(
        return_value=httpx.Response(
            200,
            text="",
            headers={"content-type": "text/html"},
        )
    )

    client = AnaptyxiClient(_config())
    try:
        with pytest.raises(ProjectNotFoundError):
            await client.find_project_by_mis(MIS)
    finally:
        await client.aclose()


@respx.mock
async def test_5xx_is_retried_then_raises_on_exhaustion():
    respx.get(f"{BASE_URL}/GetData.ashx").mock(return_value=httpx.Response(503))

    client = AnaptyxiClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.find_project_by_mis(MIS)
    finally:
        await client.aclose()


def test_client_exposes_program_period_from_config():
    client = AnaptyxiClient(_config(program_period="ANAPTYXI_2021_2027"))
    assert client.program_period == "ANAPTYXI_2021_2027"


@respx.mock
async def test_find_projects_by_beneficiary_afm_parses_results_list():
    afm = "094259216"
    for search_field in (4, 6):
        respx.get(
            f"{BASE_URL}/GetData.ashx",
            params={
                "queryType": "projects_v2",
                "outputFormat": "json",
                "searchField": search_field,
                "searchValue": afm,
                "pagesize": 1000,
                "pagenum": 0,
                "replaceEnum": "TRUE",
            },
        ).mock(return_value=httpx.Response(200, json={"results": [SAMPLE_BODY]}))

    client = AnaptyxiClient(_config())
    try:
        response = await client.find_projects_by_beneficiary_afm(afm)
    finally:
        await client.aclose()

    assert response.afm == afm
    assert response.results == [SAMPLE_BODY]


@respx.mock
async def test_find_projects_by_beneficiary_afm_parses_live_records_shape():
    afm = "094533338"
    summary = {
        "kodikos": 5010831,
        "title": "Project returned by the live list contract",
        "budget": "709333",
    }
    for search_field in (4, 6):
        respx.get(
            f"{BASE_URL}/GetData.ashx",
            params={
                "queryType": "projects_v2",
                "outputFormat": "json",
                "searchField": search_field,
                "searchValue": afm,
                "pagesize": 1000,
                "pagenum": 0,
                "replaceEnum": "TRUE",
            },
        ).mock(
            return_value=httpx.Response(
                200,
                json={"TotalRecords": 1, "Records": [summary]},
            )
        )

    client = AnaptyxiClient(_config())
    try:
        response = await client.find_projects_by_beneficiary_afm(afm)
    finally:
        await client.aclose()

    assert response.results == [summary]


@respx.mock
async def test_hydrate_project_summary_fetches_complete_detail_by_kodikos():
    summary = {"kodikos": 5010831, "title": "Summary"}
    respx.get(
        f"{BASE_URL}/GetData.ashx",
        params={
            "queryType": "projectDetails",
            "queryArgument": "5010831",
            "projectDetails": "all",
            "outputFormat": "json",
        },
    ).mock(return_value=httpx.Response(200, json=SAMPLE_BODY))

    client = AnaptyxiClient(_config())
    try:
        response = await client.hydrate_project_summary(summary)
    finally:
        await client.aclose()

    assert response.mis_code == "5010831"
    assert response.body == SAMPLE_BODY


@respx.mock
async def test_find_projects_by_beneficiary_afm_empty_results_is_not_an_error():
    afm = "999999999"
    for search_field in (4, 6):
        respx.get(
            f"{BASE_URL}/GetData.ashx",
            params={
                "queryType": "projects_v2",
                "outputFormat": "json",
                "searchField": search_field,
                "searchValue": afm,
                "pagesize": 1000,
                "pagenum": 0,
                "replaceEnum": "TRUE",
            },
        ).mock(return_value=httpx.Response(200, json={"results": []}))

    client = AnaptyxiClient(_config())
    try:
        response = await client.find_projects_by_beneficiary_afm(afm)
    finally:
        await client.aclose()

    assert response.results == []
