"""HTTP client for the published ΓΕΜΗ Open Data v1 API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying
from packages.source_clients.shared_rate_limit import SharedSlidingWindowLimiter

from .config import GemiConnectorConfig


class CompanyNotFoundError(Exception):
    def __init__(self, query: str) -> None:
        self.query = query
        super().__init__(f"no ΓΕΜΗ company found for {query}")


class GemiAuthenticationError(Exception):
    """The configured credential was rejected; never includes the key."""


class GemiInvalidResponseError(Exception):
    """A successful response did not contain the documented JSON shape."""


@dataclass(frozen=True)
class GemiCompanyResponse:
    query: str
    body: dict[str, Any]
    raw_body: bytes
    http_status: int


@dataclass(frozen=True)
class GemiSearchResponse:
    results: list[dict[str, Any]]
    raw_body: bytes
    http_status: int


@dataclass(frozen=True)
class GemiDocumentsResponse:
    gemi_number: str
    body: dict[str, Any]
    raw_body: bytes
    http_status: int


class GemiClient:
    def __init__(
        self,
        config: GemiConnectorConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.request_timeout_seconds,
            headers={"api_key": config.api_key},
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = (
            SharedSlidingWindowLimiter(
                int(config.rate_limit_per_minute),
                config.rate_limit_state_path,
            )
            if config.rate_limit_state_path
            else TokenBucket(config.rate_limit_per_minute, burst=1)
        )
        self._circuit_breaker = CircuitBreaker()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def _request(self, path: str, *, query_label: str, params: dict[str, str] | None = None) -> httpx.Response:
        self._circuit_breaker.raise_if_open()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            await self._rate_limiter.acquire()
            response = await self._http.get(path, params=params)
            if response.status_code == 404:
                return response
            if response.status_code == 401:
                raise GemiAuthenticationError(
                    "ΓΕΜΗ rejected the configured API credential (HTTP 401)"
                )
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()

        if response.status_code == 404:
            raise CompanyNotFoundError(query_label)

        response.raise_for_status()
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise GemiInvalidResponseError(
                f"ΓΕΜΗ returned non-JSON content with HTTP {response.status_code}"
            ) from exc
        if not isinstance(body, dict):
            raise GemiInvalidResponseError(
                f"ΓΕΜΗ returned {type(body).__name__}, expected a JSON object"
            )
        return body

    async def _fetch_company(self, *, path: str, query_label: str, params: dict[str, str] | None = None) -> GemiCompanyResponse:
        response = await self._request(path, query_label=query_label, params=params)
        return GemiCompanyResponse(
            query=query_label,
            body=self._json_object(response),
            raw_body=response.content,
            http_status=response.status_code,
        )

    async def find_by_vat(self, afm: str) -> GemiCompanyResponse:
        response = await self.search({"afm": afm, "resultsSize": "1"})
        if not response.results:
            raise CompanyNotFoundError(f"afm={afm}")
        body = response.results[0]
        return GemiCompanyResponse(
            query=f"afm={afm}",
            body=body,
            raw_body=response.raw_body,
            http_status=response.http_status,
        )

    async def find_by_gemi_number(self, gemi_number: str) -> GemiCompanyResponse:
        return await self._fetch_company(
            path=f"/companies/{gemi_number}", query_label=f"gemi={gemi_number}"
        )

    async def search(self, params: dict[str, str]) -> GemiSearchResponse:
        try:
            response = await self._request("/companies", query_label=f"search={params}", params=params)
        except CompanyNotFoundError:
            return GemiSearchResponse(results=[], raw_body=b"", http_status=404)
        body = self._json_object(response)
        results = body.get("searchResults") or []
        if not isinstance(results, list):
            raise GemiInvalidResponseError("ΓΕΜΗ searchResults must be a JSON array")
        return GemiSearchResponse(results=results, raw_body=response.content, http_status=response.status_code)

    async def get_company_documents(self, gemi_number: str) -> GemiDocumentsResponse:
        response = await self._request(
            f"/companies/{gemi_number}/documents", query_label=f"gemi_documents={gemi_number}"
        )
        return GemiDocumentsResponse(
            gemi_number=gemi_number,
            body=self._json_object(response),
            raw_body=response.content,
            http_status=response.status_code,
        )
