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
    InvalidReferencePayloadError,
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


@respx.mock
async def test_correction_history_follows_version_chain_and_stops_at_original():
    current = {**SAMPLE_BODY, "versionId": "v3", "correctedVersionId": "v2"}
    previous = {**SAMPLE_BODY, "versionId": "v2", "correctedVersionId": "v1"}
    original = {**SAMPLE_BODY, "versionId": "v1", "correctedVersionId": None}
    respx.get(f"{BASE_URL}/decisions/{ADA}").mock(return_value=httpx.Response(200, json=current))
    respx.get(f"{BASE_URL}/decisions/v/v2").mock(return_value=httpx.Response(200, json=previous))
    respx.get(f"{BASE_URL}/decisions/v/v1").mock(return_value=httpx.Response(200, json=original))

    client = DiavgeiaClient(_config())
    try:
        history = await client.fetch_correction_history(ADA)
    finally:
        await client.aclose()

    assert [item.body["versionId"] for item in history] == ["v3", "v2", "v1"]
    assert client.capabilities["VERSION_LOG"] == CapabilityStatus.AVAILABLE


@respx.mock
async def test_organization_unit_and_signer_reference_lookups_use_official_routes():
    respx.get(f"{BASE_URL}/organizations/org-1").mock(
        return_value=httpx.Response(200, json={"uid": "org-1", "label": "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"})
    )
    respx.get(f"{BASE_URL}/organizations/org-1/units", params={"status": "all"}).mock(
        return_value=httpx.Response(
            200,
            content=b"""<?xml version="1.0" encoding="UTF-8"?>
            <units xmlns="http://diavgeia.gov.gr/schema/v2">
              <unit>
                <uid>unit-1</uid>
                <label>Unit One</label>
                <active>true</active>
                <parentId>org-1</parentId>
              </unit>
            </units>""",
            headers={"content-type": "application/xml"},
        )
    )
    respx.get(f"{BASE_URL}/organizations/org-1/signers", params={"status": "all"}).mock(
        return_value=httpx.Response(
            200,
            content=b"""<?xml version="1.0" encoding="UTF-8"?>
            <signers xmlns="http://diavgeia.gov.gr/schema/v2">
              <signer>
                <uid>signer-1</uid>
                <firstName>Test</firstName>
                <lastName>Signer</lastName>
                <active>false</active>
                <organizationId>org-1</organizationId>
                <units><unit><uid>unit-1</uid></unit></units>
              </signer>
            </signers>""",
            headers={"content-type": "application/xml;charset=UTF-8"},
        )
    )

    client = DiavgeiaClient(_config())
    try:
        organization = await client.get_organization("org-1")
        units = await client.list_organization_units("org-1", status="all")
        signers = await client.list_organization_signers("org-1", status="all")
    finally:
        await client.aclose()

    assert organization.body["uid"] == "org-1"
    assert units.body["units"][0]["uid"] == "unit-1"
    assert units.body["units"][0]["active"] is True
    assert signers.body["signers"][0]["uid"] == "signer-1"
    assert signers.body["signers"][0]["active"] is False
    assert signers.body["signers"][0]["units"]["unit"]["uid"] == "unit-1"
    assert client.capabilities["ORGANIZATION_LOOKUP"] == CapabilityStatus.AVAILABLE
    assert client.capabilities["SIGNER_LOOKUP"] == CapabilityStatus.AVAILABLE


@respx.mock
async def test_reference_endpoint_rejects_unexpected_html_success_response():
    respx.get(
        f"{BASE_URL}/organizations/org-1/units",
        params={"status": "all"},
    ).mock(
        return_value=httpx.Response(
            200,
            text="<html>maintenance</html>",
            headers={"content-type": "text/html"},
        )
    )

    client = DiavgeiaClient(_config())
    try:
        with pytest.raises(InvalidReferencePayloadError, match="unexpected XML root"):
            await client.list_organization_units("org-1", status="all")
        assert (
            client.capabilities["ORGANIZATION_LOOKUP"]
            == CapabilityStatus.DEGRADED
        )
    finally:
        await client.aclose()


@respx.mock
async def test_reference_dictionary_is_cached_for_one_ingestion_run():
    route = respx.get(f"{BASE_URL}/organizations/org-1").mock(
        return_value=httpx.Response(
            200,
            json={"uid": "org-1", "label": "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"},
        )
    )

    client = DiavgeiaClient(_config())
    try:
        first = await client.get_organization("org-1")
        second = await client.get_organization("org-1")
    finally:
        await client.aclose()

    assert first is second
    assert route.call_count == 1
