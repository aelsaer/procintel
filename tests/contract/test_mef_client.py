"""ΜΕΦ `/api/spendings` wire-contract tests with mocked HTTP."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from services.ingestion.connectors.mef.client import (
    MefClient,
    MefUpstreamUnavailableError,
)
from services.ingestion.connectors.mef.config import MefConnectorConfig

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "mef" / "expenses_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BASE_URL = "https://mef.example.test"
AFM = "090000045"


def _config(**overrides) -> MefConnectorConfig:
    return MefConnectorConfig(
        base_url=BASE_URL,
        rate_limit_per_minute=6000,
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        lookup_years=overrides.pop("lookup_years", (2025,)),
        **overrides,
    )


@respx.mock
async def test_find_expenses_uses_spendings_search_and_filters_exact_issuer_afm():
    broad_result = {
        **SAMPLE_BODY,
        "items": [
            *SAMPLE_BODY["items"],
            {**SAMPLE_BODY["items"][0], "uid": "UNRELATED", "issuer_afm": "099999999"},
        ],
        "count": 3,
    }
    probe = respx.get(
        f"{BASE_URL}/api/spendings",
        params={"year": "2025", "limit": "1", "offset": "0"},
    ).mock(return_value=httpx.Response(200, json={"items": [{}], "count": 3}))
    route = respx.get(
        f"{BASE_URL}/api/spendings",
        params={"year": "2025", "searchTerm": AFM, "limit": "200", "offset": "0"},
    ).mock(return_value=httpx.Response(200, json=broad_result))
    client = MefClient(_config())
    try:
        response = await client.find_expenses_by_recipient_afm(AFM)
    finally:
        await client.aclose()

    assert probe.call_count == route.call_count == 1
    assert response.recipient_afm == AFM
    assert response.lookup_years == (2025,)
    assert response.expenses == SAMPLE_BODY["items"]
    assert response.http_status == 200


@respx.mock
async def test_empty_items_list_is_not_an_error():
    probe = respx.get(
        f"{BASE_URL}/api/spendings",
        params={"year": "2025", "limit": "1", "offset": "0"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "count": 0, "sum": "0,00"},
        )
    )
    client = MefClient(_config())
    try:
        response = await client.find_expenses_by_recipient_afm(AFM)
    finally:
        await client.aclose()
    assert response.expenses == []
    assert probe.call_count == 1


@respx.mock
async def test_5xx_is_retried_then_raises_on_exhaustion():
    respx.get(
        f"{BASE_URL}/api/spendings",
        params={"year": "2025", "limit": "1", "offset": "0"},
    ).mock(return_value=httpx.Response(503))
    client = MefClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(MefUpstreamUnavailableError, match="TransientServerError"):
            await client.find_expenses_by_recipient_afm(AFM)
    finally:
        await client.aclose()


@respx.mock
async def test_4xx_raises_http_status_error():
    respx.get(
        f"{BASE_URL}/api/spendings",
        params={"year": "2025", "limit": "1", "offset": "0"},
    ).mock(return_value=httpx.Response(404))
    client = MefClient(_config())
    try:
        with pytest.raises(MefUpstreamUnavailableError, match="HTTPStatusError"):
            await client.find_expenses_by_recipient_afm(AFM)
    finally:
        await client.aclose()


@respx.mock
async def test_lookup_is_partitioned_by_year_and_combines_exact_matches():
    for year in (2026, 2025):
        respx.get(
            f"{BASE_URL}/api/spendings",
            params={"year": str(year), "limit": "1", "offset": "0"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={"items": [{}], "count": 1},
            )
        )
        respx.get(
            f"{BASE_URL}/api/spendings",
            params={
                "year": str(year),
                "searchTerm": AFM,
                "limit": "200",
                "offset": "0",
            },
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            **SAMPLE_BODY["items"][0],
                            "uid": f"expense-{year}",
                            "year": year,
                        }
                    ],
                    "count": 1,
                },
            )
        )

    client = MefClient(_config(lookup_years=(2026, 2025)))
    try:
        response = await client.find_expenses_by_recipient_afm(AFM)
    finally:
        await client.aclose()

    assert response.lookup_years == (2026, 2025)
    assert [item["uid"] for item in response.expenses] == [
        "expense-2026",
        "expense-2025",
    ]


@respx.mock
async def test_empty_year_probe_is_cached_across_supplier_lookups():
    probe = respx.get(
        f"{BASE_URL}/api/spendings",
        params={"year": "2026", "limit": "1", "offset": "0"},
    ).mock(return_value=httpx.Response(200, json={"items": [], "count": 0}))

    client = MefClient(_config(lookup_years=(2026,)))
    try:
        first = await client.find_expenses_by_recipient_afm(AFM)
        second = await client.find_expenses_by_recipient_afm("099999999")
    finally:
        await client.aclose()

    assert first.expenses == second.expenses == []
    assert probe.call_count == 1
