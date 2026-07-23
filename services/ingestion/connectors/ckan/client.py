"""Generic CKAN Action API client (spec §3.6, §22).

Implements the three operations description.txt names explicitly —
`package_search`, `package_show`, `resource_search` — plus a plain resource
downloader (`fetch_resource_bytes`) needed to actually read a dataset's CSV/
JSON/XML/XLSX content once a `package_show` response points at it. These are
CKAN's own standard Action API paths (`/api/3/action/<name>`), stable across
any CKAN deployment — but the exact query-parameter names/limits still need
confirming against the live data.gov.gr deployment (description.txt's own
caveat), so treat the request shape here as a well-known-but-unconfirmed
baseline, same as VIES's public WSDL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying

from .config import CkanConnectorConfig


class CkanActionError(Exception):
    """CKAN Action API returned success=false (dataset/resource not found, bad query, ...)."""


@dataclass(frozen=True)
class PackageSearchResponse:
    query: str
    count: int
    results: list[dict[str, Any]]


@dataclass(frozen=True)
class PackageShowResponse:
    catalog_dataset_id: str
    title: str
    publisher: str | None
    license_code: str | None
    resources: list[dict[str, Any]]
    raw_result: dict[str, Any]


@dataclass(frozen=True)
class ResourceSearchResponse:
    query: str
    count: int
    results: list[dict[str, Any]]


@dataclass(frozen=True)
class ResourceBytesResponse:
    content: bytes
    http_status: int


class CkanClient:
    def __init__(self, config: CkanConnectorConfig, http_client: httpx.AsyncClient | None = None) -> None:
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

    async def _request(self, method_get, *args, **kwargs) -> httpx.Response:
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            response = await method_get(*args, **kwargs)
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()
        response.raise_for_status()
        return response

    async def _get_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(self._http.get, f"/api/3/action/{action}", params=params)
        body = response.json()
        if not body.get("success", False):
            raise CkanActionError(f"{action} returned success=false: {body.get('error')}")
        return body["result"]

    async def package_search(self, query: str, rows: int = 100) -> PackageSearchResponse:
        # TODO(confirm against live deployment): query param name (`q`) and page size cap.
        result = await self._get_action("package_search", {"q": query, "rows": rows})
        return PackageSearchResponse(query=query, count=result.get("count", 0), results=result.get("results", []))

    async def package_show(self, dataset_id: str) -> PackageShowResponse:
        result = await self._get_action("package_show", {"id": dataset_id})
        return PackageShowResponse(
            catalog_dataset_id=result.get("id") or result.get("name") or dataset_id,
            title=result.get("title", ""),
            publisher=(result.get("organization") or {}).get("title"),
            license_code=result.get("license_id"),
            resources=result.get("resources", []),
            raw_result=result,
        )

    async def resource_search(self, query: str) -> ResourceSearchResponse:
        # TODO(confirm against live deployment): query syntax (CKAN's docs
        # describe `field:value` pairs, e.g. `format:CSV`).
        result = await self._get_action("resource_search", {"query": query})
        return ResourceSearchResponse(query=query, count=result.get("count", 0), results=result.get("results", []))

    async def fetch_resource_bytes(self, resource_url: str) -> ResourceBytesResponse:
        """Plain download of a dataset resource (CSV/JSON/XML/XLSX) — not an
        Action API call; `resource_url` is whatever `package_show` reported
        for the resource, an absolute URL that may live on a different host
        than the Action API base."""
        response = await self._request(self._http.get, resource_url)
        return ResourceBytesResponse(content=response.content, http_status=response.status_code)
