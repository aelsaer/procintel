"""Connector-level tests against mocked HTTP (respx) — no live Διαύγεια
access required or attempted."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from packages.source_clients.retry import TransientServerError
from services.ingestion.connectors.diavgeia.client import (
    CapabilityStatus,
    DecisionNotFoundError,
    DiavgeiaClient,
)
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "diavgeia" / "decision_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

BASE_URL = "https://diavgeia.example.test"
ADA = "7Α1Η465ΦΘΘ-ΘΙΚ"


def _config(**overrides) -> DiavgeiaConnectorConfig:
    return DiavgeiaConnectorConfig(
        base_url=BASE_URL,
        rate_limit_per_minute=6000,
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        **overrides,
    )


@respx.mock
async def test_fetch_decision_by_ada_parses_response_and_marks_capability_available():
    respx.get(f"{BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(200, json=SAMPLE_BODY))

    client = DiavgeiaClient(_config())
    try:
        assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.UNKNOWN
        response = await client.fetch_decision_by_ada(ADA)
        assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.AVAILABLE
    finally:
        await client.aclose()

    assert response.ada == ADA
    assert response.body["subject"] == SAMPLE_BODY["subject"]


@respx.mock
async def test_404_raises_decision_not_found_and_still_marks_capability_available():
    respx.get(f"{BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(404))

    client = DiavgeiaClient(_config())
    try:
        with pytest.raises(DecisionNotFoundError):
            await client.fetch_decision_by_ada(ADA)
        # a 404 means the API answered — direct fetch is working, just no
        # decision under this ΑΔΑ. Not a degraded capability.
        assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.AVAILABLE
    finally:
        await client.aclose()


@respx.mock
async def test_5xx_is_retried_and_marks_capability_degraded_on_exhaustion():
    respx.get(f"{BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(503))

    client = DiavgeiaClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.fetch_decision_by_ada(ADA)
        assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.DEGRADED
    finally:
        await client.aclose()


@respx.mock
async def test_5xx_then_success_recovers_capability_to_available():
    route = respx.get(f"{BASE_URL}/decisions/{ADA}")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json=SAMPLE_BODY)]

    client = DiavgeiaClient(_config())
    try:
        response = await client.fetch_decision_by_ada(ADA)
    finally:
        await client.aclose()

    assert response.ada == ADA
    assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.AVAILABLE


@respx.mock
async def test_search_decisions_parses_results_and_marks_capability_available():
    respx.get(f"{BASE_URL}/search", params={"org": "ΔΗΜΟΣ ΔΟΚΙΜΗΣ", "subject": "καθαρισμού"}).mock(
        return_value=httpx.Response(200, json={"decisions": [SAMPLE_BODY]})
    )

    client = DiavgeiaClient(_config())
    try:
        assert client.capabilities["SEARCH"] == CapabilityStatus.UNKNOWN
        response = await client.search_decisions(organization_query="ΔΗΜΟΣ ΔΟΚΙΜΗΣ", title_query="καθαρισμού")
        assert client.capabilities["SEARCH"] == CapabilityStatus.AVAILABLE
    finally:
        await client.aclose()

    assert response.results == [SAMPLE_BODY]


@respx.mock
async def test_search_decisions_advanced_parses_results_and_marks_capability_available():
    respx.get(
        f"{BASE_URL}/search",
        params={"org": "ΔΗΜΟΣ ΔΟΚΙΜΗΣ", "type": "ΑΝΑΘΕΣΗ", "protocol": "12345/2025"},
    ).mock(return_value=httpx.Response(200, json={"decisions": [SAMPLE_BODY]}))

    client = DiavgeiaClient(_config())
    try:
        assert client.capabilities["ADVANCED_SEARCH"] == CapabilityStatus.UNKNOWN
        response = await client.search_decisions_advanced(
            organization_query="ΔΗΜΟΣ ΔΟΚΙΜΗΣ", decision_type="ΑΝΑΘΕΣΗ", protocol_number="12345/2025"
        )
        assert client.capabilities["ADVANCED_SEARCH"] == CapabilityStatus.AVAILABLE
    finally:
        await client.aclose()

    assert response.results == [SAMPLE_BODY]


@respx.mock
async def test_advanced_search_5xx_marks_advanced_search_degraded_but_not_search_or_direct_fetch():
    respx.get(f"{BASE_URL}/search", params={"subject": "x"}).mock(return_value=httpx.Response(503))

    client = DiavgeiaClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.search_decisions_advanced(title_query="x")
        assert client.capabilities["ADVANCED_SEARCH"] == CapabilityStatus.DEGRADED
        assert client.capabilities["SEARCH"] == CapabilityStatus.UNKNOWN
        assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.UNKNOWN
    finally:
        await client.aclose()


@respx.mock
async def test_search_5xx_marks_search_degraded_but_not_direct_fetch():
    respx.get(f"{BASE_URL}/search", params={"subject": "x"}).mock(return_value=httpx.Response(503))

    client = DiavgeiaClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.search_decisions(title_query="x")
        assert client.capabilities["SEARCH"] == CapabilityStatus.DEGRADED
        # §17.3: a degraded SEARCH capability must never block direct fetch —
        # separate circuit breakers, so this call still goes through fine.
        assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.UNKNOWN
        respx.get(f"{BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(200, json=SAMPLE_BODY))
        await client.fetch_decision_by_ada(ADA)
        assert client.capabilities["DIRECT_ADA_FETCH"] == CapabilityStatus.AVAILABLE
    finally:
        await client.aclose()
