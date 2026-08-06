"""HTTP client for the public ΜΕΦ Open Data API (spec §3.5, §20)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import (
    CircuitBreaker,
    CircuitOpenError,
    RateLimitedError,
    TransientServerError,
    raise_for_retryable_status,
    retrying,
)

from .config import MefConnectorConfig


@dataclass(frozen=True)
class MefExpensesResponse:
    recipient_afm: str
    lookup_years: tuple[int, ...]
    expenses: list[dict[str, Any]]
    raw_body: bytes
    http_status: int


class MefUpstreamUnavailableError(RuntimeError):
    """The public MEF endpoint could not satisfy a lookup after bounded retries."""


class MefClient:
    def __init__(
        self,
        config: MefConnectorConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.request_timeout_seconds
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = TokenBucket(config.rate_limit_per_minute)
        self._circuit_breaker = CircuitBreaker()
        self._empty_years: dict[int, bool] = {}

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _get_spendings(
        self,
        params: dict[str, str],
    ) -> httpx.Response:
        try:
            self._circuit_breaker.raise_if_open()
            await self._rate_limiter.acquire()

            @retrying(max_attempts=self._config.max_retry_attempts)
            async def _do_request() -> httpx.Response:
                response = await self._http.get("/api/spendings", params=params)
                raise_for_retryable_status(response)
                return response

            response = await _do_request()
            response.raise_for_status()
        except (
            CircuitOpenError,
            RateLimitedError,
            TransientServerError,
            httpx.HTTPError,
        ) as exc:
            self._circuit_breaker.record_failure()
            raise MefUpstreamUnavailableError(
                f"MEF /api/spendings is unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        self._circuit_breaker.record_success()
        return response

    async def _year_is_empty(self, year: int) -> bool:
        cached = self._empty_years.get(year)
        if cached is not None:
            return cached
        response = await self._get_spendings(
            {"year": str(year), "limit": "1", "offset": "0"}
        )
        body = response.json()
        is_empty = int(body.get("count") or 0) == 0
        self._empty_years[year] = is_empty
        return is_empty

    async def find_expenses_by_recipient_afm(self, afm: str) -> MefExpensesResponse:
        afm_digits = "".join(character for character in afm if character.isdigit())
        expenses: list[dict[str, Any]] = []
        http_status = 200
        lookup_years = self._config.lookup_years or (
            datetime.now(timezone.utc).year,
        )

        for year in lookup_years:
            if await self._year_is_empty(year):
                continue
            for page in range(self._config.max_pages_per_lookup):
                offset = page * self._config.page_size
                response = await self._get_spendings(
                    {
                        "year": str(year),
                        "searchTerm": afm_digits,
                        "limit": str(self._config.page_size),
                        "offset": str(offset),
                    }
                )
                http_status = response.status_code
                body = response.json()
                items = body.get("items") or []
                expenses.extend(
                    item
                    for item in items
                    if "".join(
                        character
                        for character in str(item.get("issuer_afm") or "")
                        if character.isdigit()
                    )
                    == afm_digits
                )
                total_count = int(body.get("count") or len(items))
                if not items or offset + len(items) >= total_count:
                    break

        raw_body = json.dumps(
            {"items": expenses, "lookup_years": lookup_years},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

        return MefExpensesResponse(
            recipient_afm=afm,
            lookup_years=lookup_years,
            expenses=expenses,
            raw_body=raw_body,
            http_status=http_status,
        )
