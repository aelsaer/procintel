"""HTTP client for the public TED Search API v3 (spec §3.8, §21.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying

from .config import TedConnectorConfig


TED_SEARCH_FIELDS = (
    "publication-number",
    "notice-identifier",
    "notice-title",
    "notice-type",
    "form-type",
    "buyer-name",
    "buyer-identifier",
    "buyer-country",
    "winner-name",
    "winner-identifier",
    "winner-country",
    "classification-cpv",
    "estimated-value-proc",
    "estimated-value-lot",
    "result-value-notice",
    "procedure-type",
    "place-of-performance",
    "publication-date",
)

TED_COUNTRY_CODES = {"GR": "GRC"}


@dataclass(frozen=True)
class TedSearchPage:
    notices: list[dict[str, Any]]
    is_last_page: bool
    raw_body: bytes
    http_status: int


class TedClient:
    def __init__(
        self,
        config: TedConnectorConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.request_timeout_seconds
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = TokenBucket(config.rate_limit_per_minute)
        self._circuit_breaker = CircuitBreaker()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def search_notices(
        self, *, country: str, date_from: date, date_to: date, page: int
    ) -> TedSearchPage:
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            country_code = TED_COUNTRY_CODES.get(country.upper(), country.upper())
            expert_query = (
                f"buyer-country = {country_code} "
                f"AND publication-date >= {date_from:%Y%m%d} "
                f"AND publication-date <= {date_to:%Y%m%d}"
            )
            response = await self._http.post(
                "/v3/notices/search",
                json={
                    "query": expert_query,
                    "fields": list(TED_SEARCH_FIELDS),
                    "page": page + 1,
                    "limit": 250,
                    "scope": "ALL",
                    "checkQuerySyntax": False,
                    "paginationMode": "PAGE_NUMBER",
                },
            )
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()

        response.raise_for_status()
        body = response.json()
        notices = body.get("notices") or []
        total_count = int(body.get("totalNoticeCount") or 0)
        is_last_page = not notices or (page + 1) * 250 >= total_count

        return TedSearchPage(
            notices=notices,
            is_last_page=is_last_page,
            raw_body=response.content,
            http_status=response.status_code,
        )
