from __future__ import annotations

from datetime import date

import httpx
import pytest

from packages.source_clients.shared_rate_limit import SharedSlidingWindowLimiter
from services.ingestion.connectors.anaptyxi.client import AnaptyxiClient
from services.ingestion.connectors.anaptyxi.config import AnaptyxiConnectorConfig
from services.ingestion.connectors.ckan.client import CkanClient
from services.ingestion.connectors.ckan.config import CkanConnectorConfig
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.khmdhs.client import KhmdhsClient
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.mef.client import MefClient
from services.ingestion.connectors.mef.config import MefConnectorConfig
from services.ingestion.connectors.ted.client import TedClient
from services.ingestion.connectors.ted.config import TedConnectorConfig
from services.ingestion.connectors.vies.client import ViesClient
from services.ingestion.connectors.vies.config import ViesConnectorConfig


def test_shared_limiter_combines_budget_across_instances(tmp_path) -> None:
    now = [1000.0]
    path = tmp_path / "provider.json"
    first = SharedSlidingWindowLimiter(2, str(path), clock=lambda: now[0])
    second = SharedSlidingWindowLimiter(2, str(path), clock=lambda: now[0])

    assert first._reserve_or_wait() == 0
    assert second._reserve_or_wait() == 0
    assert first._reserve_or_wait() == 60

    now[0] += 61
    assert second._reserve_or_wait() == 0


def test_shared_limiter_recovers_from_invalid_state(tmp_path) -> None:
    path = tmp_path / "provider.json"
    path.write_text('{"invalid": true}', encoding="utf-8")
    limiter = SharedSlidingWindowLimiter(1, str(path), clock=lambda: 1000.0)

    assert limiter._reserve_or_wait() == 0


@pytest.mark.asyncio
async def test_public_clients_use_shared_limiter_when_state_path_is_configured(
    tmp_path,
) -> None:
    async with httpx.AsyncClient() as http:
        khmdhs = KhmdhsClient(
            KhmdhsConnectorConfig(
                rate_limit_state_path=str(tmp_path / "khmdhs.json")
            ),
            http_client=http,
        )
        diavgeia = DiavgeiaClient(
            DiavgeiaConnectorConfig(
                rate_limit_state_path=str(tmp_path / "diavgeia.json")
            ),
            http_client=http,
        )

        assert isinstance(khmdhs.request_rate_limiter, SharedSlidingWindowLimiter)
        assert isinstance(diavgeia.request_rate_limiter, SharedSlidingWindowLimiter)


class _CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, cost: float = 1.0) -> None:
        assert cost == 1.0
        self.calls += 1


@pytest.mark.asyncio
async def test_khmdhs_retries_consume_provider_budget(monkeypatch) -> None:
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(
                200,
                json={"content": [], "last": True},
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        response.request = request
        return response

    monkeypatch.setattr("packages.source_clients.retry.random.uniform", lambda *_: 0)
    limiter = _CountingLimiter()
    async with httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    ) as http:
        client = KhmdhsClient(
            KhmdhsConnectorConfig(
                base_url="https://example.test",
                max_retry_attempts=2,
            ),
            http_client=http,
            rate_limiter=limiter,
        )
        await client.fetch_contract_page(
            page=0,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 1),
        )

    assert limiter.calls == 2


@pytest.mark.asyncio
async def test_all_bulk_provider_clients_use_cross_process_limiters(tmp_path) -> None:
    state = str(tmp_path / "provider.json")
    async with httpx.AsyncClient() as http:
        clients = [
            AnaptyxiClient(
                AnaptyxiConnectorConfig(
                    base_url="https://example.test",
                    rate_limit_state_path=state,
                ),
                http_client=http,
            ),
            CkanClient(
                CkanConnectorConfig(
                    base_url="https://example.test",
                    rate_limit_state_path=state,
                ),
                http_client=http,
            ),
            MefClient(
                MefConnectorConfig(
                    base_url="https://example.test",
                    rate_limit_state_path=state,
                ),
                http_client=http,
            ),
            TedClient(
                TedConnectorConfig(
                    base_url="https://example.test",
                    rate_limit_state_path=state,
                ),
                http_client=http,
            ),
            ViesClient(
                ViesConnectorConfig(
                    base_url="https://example.test",
                    rate_limit_state_path=state,
                ),
                http_client=http,
            ),
        ]

        assert all(
            isinstance(client._rate_limiter, SharedSlidingWindowLimiter)
            for client in clients
        )
