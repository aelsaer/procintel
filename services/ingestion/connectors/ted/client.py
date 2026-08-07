"""HTTP client for the public TED Search API v3 (spec §3.8, §21.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from packages.source_clients.shared_rate_limit import configured_rate_limiter
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
    "procedure-identifier",
    "place-of-performance",
    "publication-date",
    "deadline-receipt-request",
    "deadline-receipt-tender-date-lot",
    "deadline-receipt-tender-time-lot",
    "deadline-receipt-request-date-lot",
    "deadline-receipt-request-time-lot",
    "notice-version",
    "customization-id",
    "previous-notice-id-proc",
    "modification-previous-notice-identifier",
    "change-notice-version-identifier",
)

TED_COUNTRY_CODES = {
    "AT": "AUT",
    "BE": "BEL",
    "BG": "BGR",
    "HR": "HRV",
    "CY": "CYP",
    "CZ": "CZE",
    "DE": "DEU",
    "DK": "DNK",
    "EE": "EST",
    "ES": "ESP",
    "FI": "FIN",
    "FR": "FRA",
    "GR": "GRC",
    "HU": "HUN",
    "IE": "IRL",
    "IS": "ISL",
    "IT": "ITA",
    "LI": "LIE",
    "LT": "LTU",
    "LU": "LUX",
    "LV": "LVA",
    "MT": "MLT",
    "NL": "NLD",
    "NO": "NOR",
    "PL": "POL",
    "PT": "PRT",
    "RO": "ROU",
    "SE": "SWE",
    "SI": "SVN",
    "SK": "SVK",
}


@dataclass(frozen=True)
class TedSearchPage:
    notices: list[dict[str, Any]]
    is_last_page: bool
    raw_body: bytes
    http_status: int
    iteration_next_token: str | None = None


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
        self._rate_limiter = configured_rate_limiter(
            config.rate_limit_per_minute,
            config.rate_limit_state_path,
        )
        self._circuit_breaker = CircuitBreaker()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def search_notices(
        self,
        *,
        country: str,
        date_from: date,
        date_to: date,
        page: int = 0,
        iteration_next_token: str | None = None,
    ) -> TedSearchPage:
        self._circuit_breaker.raise_if_open()
        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            await self._rate_limiter.acquire()
            country_code = TED_COUNTRY_CODES.get(country.upper(), country.upper())
            expert_query = (
                f"buyer-country = {country_code} "
                f"AND publication-date >= {date_from:%Y%m%d} "
                f"AND publication-date <= {date_to:%Y%m%d}"
            )
            request_body: dict[str, Any] = {
                "query": expert_query,
                "fields": list(TED_SEARCH_FIELDS),
                "limit": 250,
                "scope": "ALL",
                "checkQuerySyntax": False,
                "paginationMode": "ITERATION",
                "onlyLatestVersions": False,
            }
            if iteration_next_token:
                request_body["iterationNextToken"] = iteration_next_token
            response = await self._http.post(
                "/v3/notices/search",
                json=request_body,
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
        next_token = body.get("iterationNextToken")
        is_last_page = not notices or not next_token

        return TedSearchPage(
            notices=notices,
            is_last_page=is_last_page,
            raw_body=response.content,
            http_status=response.status_code,
            iteration_next_token=str(next_token) if next_token else None,
        )
