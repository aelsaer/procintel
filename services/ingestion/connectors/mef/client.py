"""HTTP client for the public ΜΕΦ Open Data API (spec §3.5, §20)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying

from .config import MefConnectorConfig


@dataclass(frozen=True)
class MefExpensesResponse:
    recipient_afm: str
    expenses: list[dict[str, Any]]
    raw_body: bytes
    http_status: int


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

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def find_expenses_by_recipient_afm(self, afm: str) -> MefExpensesResponse:
        afm_digits = "".join(character for character in afm if character.isdigit())
        expenses: list[dict[str, Any]] = []
        http_status = 200

        for page in range(self._config.max_pages_per_lookup):
            self._circuit_breaker.raise_if_open()
            await self._rate_limiter.acquire()
            offset = page * self._config.page_size

            @retrying(max_attempts=self._config.max_retry_attempts)
            async def _do_request() -> httpx.Response:
                response = await self._http.get(
                    "/api/spendings",
                    params={
                        "searchTerm": afm_digits,
                        "limit": str(self._config.page_size),
                        "offset": str(offset),
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
            http_status = response.status_code
            body = response.json()
            items = body.get("items") or []
            expenses.extend(
                item
                for item in items
                if "".join(character for character in str(item.get("issuer_afm") or "") if character.isdigit())
                == afm_digits
            )
            total_count = int(body.get("count") or len(items))
            if not items or offset + len(items) >= total_count:
                break

        raw_body = json.dumps({"items": expenses}, ensure_ascii=False, sort_keys=True).encode("utf-8")

        return MefExpensesResponse(
            recipient_afm=afm,
            expenses=expenses,
            raw_body=raw_body,
            http_status=http_status,
        )
