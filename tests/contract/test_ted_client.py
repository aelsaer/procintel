"""TED Search API v3 wire-contract tests with mocked HTTP."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from packages.source_clients.retry import TransientServerError
from services.ingestion.connectors.ted.client import TED_SEARCH_FIELDS, TedClient
from services.ingestion.connectors.ted.config import TedConnectorConfig

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "ted" / "notice_sample.json"
SAMPLE_NOTICE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
SAMPLE_BODY = {
    "notices": [SAMPLE_NOTICE],
    "totalNoticeCount": 1,
    "iterationNextToken": None,
    "timedOut": False,
}
BASE_URL = "https://ted.example.test"


def _config(**overrides) -> TedConnectorConfig:
    return TedConnectorConfig(
        base_url=BASE_URL,
        rate_limit_per_minute=6000,
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        **overrides,
    )


@respx.mock
async def test_search_notices_posts_official_v3_body_and_parses_total_count():
    route = respx.post(f"{BASE_URL}/v3/notices/search").mock(
        return_value=httpx.Response(200, json=SAMPLE_BODY)
    )
    client = TedClient(_config())
    try:
        page = await client.search_notices(
            country="GR", date_from=date(2025, 1, 1), date_to=date(2025, 1, 30), page=0
        )
    finally:
        await client.aclose()

    request_body = json.loads(route.calls[0].request.content)
    assert request_body == {
        "query": "buyer-country = GRC AND publication-date >= 20250101 AND publication-date <= 20250130",
        "fields": list(TED_SEARCH_FIELDS),
        "page": 1,
        "limit": 250,
        "scope": "ALL",
        "checkQuerySyntax": False,
        "paginationMode": "PAGE_NUMBER",
    }
    assert page.is_last_page is True
    assert page.notices[0]["publication-number"] == "123456-2025"


@respx.mock
async def test_total_count_drives_page_number_pagination():
    body = {"notices": [SAMPLE_NOTICE], "totalNoticeCount": 501}
    route = respx.post(f"{BASE_URL}/v3/notices/search").mock(
        return_value=httpx.Response(200, json=body)
    )
    client = TedClient(_config())
    try:
        page = await client.search_notices(
            country="GR", date_from=date(2025, 1, 1), date_to=date(2025, 1, 30), page=1
        )
    finally:
        await client.aclose()
    assert json.loads(route.calls[0].request.content)["page"] == 2
    assert page.is_last_page is False


@respx.mock
async def test_5xx_is_retried_then_raises_on_exhaustion():
    respx.post(f"{BASE_URL}/v3/notices/search").mock(return_value=httpx.Response(503))
    client = TedClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.search_notices(
                country="GR", date_from=date(2025, 1, 1), date_to=date(2025, 1, 30), page=0
            )
    finally:
        await client.aclose()

