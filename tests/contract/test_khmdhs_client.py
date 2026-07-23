"""Connector-level tests against mocked HTTP (respx) — no live ΚΗΜΔΗΣ access
required or attempted. Response envelope field names (`data`/`isLastPage`)
match the client's current TODO-marked guess; update both together if the
real API turns out to differ (docs/source-contracts/khmdhs.md)."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from packages.source_clients.retry import TransientServerError
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "contract_sample.json"
SAMPLE_BODY = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

BASE_URL = "https://khmdhs.example.test"


def _config(**overrides) -> KhmdhsConnectorConfig:
    return KhmdhsConnectorConfig(
        base_url=BASE_URL,
        rate_limit_per_minute=6000,  # fast for tests
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        **overrides,
    )


@respx.mock
async def test_fetch_contract_page_parses_records_and_last_page_flag():
    body = {"content": SAMPLE_BODY["data"], "last": True}
    route = respx.post(f"{BASE_URL}/khmdhs-opendata/contract", params={"page": 0}).mock(
        return_value=httpx.Response(200, json=body)
    )

    client = KhmdhsClient(_config())
    try:
        page = await client.fetch_resource_page(
            resource="contract",
            page=0,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 1, 30),
            reference_number="25SYMV012345678",
        )
    finally:
        await client.aclose()

    assert page.is_last_page is True
    assert len(page.records) == 2
    assert page.records[0]["referenceNumber"] == "25SYMV012345678"
    assert route.calls[0].request.headers["accept"] == "application/json"
    assert json.loads(route.calls[0].request.content) == {
        "dateFrom": "2025-01-01",
        "dateTo": "2025-01-30",
        "referenceNumber": "25SYMV012345678",
    }


@respx.mock
async def test_fetch_resource_page_works_for_non_contract_resources():
    payment_fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "khmdhs" / "payment_sample.json").read_text(
            encoding="utf-8"
        )
    )
    respx.post(f"{BASE_URL}/khmdhs-opendata/payment", params={"page": 0}).mock(
        return_value=httpx.Response(200, json=payment_fixture)
    )

    client = KhmdhsClient(_config())
    try:
        page = await client.fetch_payment_page(page=0, date_from=date(2025, 1, 1), date_to=date(2025, 1, 30))
    finally:
        await client.aclose()

    assert page.resource == "payment"
    assert page.records[0]["referenceNumber"] == "25PAY000444555"


@respx.mock
async def test_unknown_resource_is_rejected_before_any_request():
    client = KhmdhsClient(_config())
    try:
        with pytest.raises(ValueError):
            await client.fetch_resource_page(
                resource="not-a-resource",  # type: ignore[arg-type]
                page=0,
                date_from=date(2025, 1, 1),
                date_to=date(2025, 1, 30),
            )
    finally:
        await client.aclose()


@respx.mock
async def test_429_with_retry_after_is_retried_and_eventually_succeeds():
    route = respx.post(f"{BASE_URL}/khmdhs-opendata/contract", params={"page": 0})
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json=SAMPLE_BODY),
    ]

    client = KhmdhsClient(_config())
    try:
        page = await client.fetch_contract_page(page=0, date_from=date(2025, 1, 1), date_to=date(2025, 1, 30))
    finally:
        await client.aclose()

    assert page.is_last_page is True
    assert route.call_count == 2


@respx.mock
async def test_5xx_is_retried_with_backoff_and_eventually_succeeds():
    route = respx.post(f"{BASE_URL}/khmdhs-opendata/contract", params={"page": 0})
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, json=SAMPLE_BODY),
    ]

    client = KhmdhsClient(_config())
    try:
        page = await client.fetch_contract_page(page=0, date_from=date(2025, 1, 1), date_to=date(2025, 1, 30))
    finally:
        await client.aclose()

    assert page.is_last_page is True
    assert route.call_count == 2


@respx.mock
async def test_fetch_adam_chain_parses_response_and_uses_get():
    respx.get(f"{BASE_URL}/khmdhs-opendata/adamChain/25SYMV012345678").mock(
        return_value=httpx.Response(200, json={"notices": ["25PROC000000001*"], "contracts": ["25SYMV012345678"]})
    )

    client = KhmdhsClient(_config())
    try:
        response = await client.fetch_adam_chain("25SYMV012345678")
    finally:
        await client.aclose()

    assert response.reference_number == "25SYMV012345678"
    assert response.body["notices"][0] == "25PROC000000001*"
    assert response.http_status == 200


@respx.mock
async def test_exhausted_retries_raise_and_open_the_circuit():
    respx.post(f"{BASE_URL}/khmdhs-opendata/contract", params={"page": 0}).mock(
        return_value=httpx.Response(503)
    )

    client = KhmdhsClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.fetch_contract_page(page=0, date_from=date(2025, 1, 1), date_to=date(2025, 1, 30))
    finally:
        await client.aclose()
