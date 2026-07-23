"""HTTP client for ΑΝΑΠΤΥΞΗ.gov.gr (spec §3.4, §19).

Endpoint path is a best-effort guess (`GET /projects?misCode=`, a common
REST convention for this kind of lookup) — description.txt confirms the
*resource types* the 2014-2020 Open Data API exposes (projects, subprojects,
beneficiaries, contractors, budgets, payments) but not the exact request
shape. Isolated to `find_project_by_mis` so fixing it against the real API
is a one-line change, same discipline as every other connector.

Only lookup-by-MIS is implemented — this connector supports join hierarchy
Level 1 (§19.2) only; see `resolve.py`'s module docstring for what's
deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying

from .config import AnaptyxiConnectorConfig


class ProjectNotFoundError(Exception):
    def __init__(self, mis_code: str) -> None:
        self.mis_code = mis_code
        super().__init__(f"no ΑΝΑΠΤΥΞΗ project found for MIS {mis_code}")


@dataclass(frozen=True)
class AnaptyxiProjectResponse:
    mis_code: str
    body: dict[str, Any]
    raw_body: bytes
    http_status: int


@dataclass(frozen=True)
class AnaptyxiBeneficiarySearchResponse:
    afm: str
    results: list[dict[str, Any]]
    raw_body: bytes
    http_status: int


class AnaptyxiClient:
    def __init__(
        self,
        config: AnaptyxiConnectorConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self.program_period = config.program_period
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.request_timeout_seconds
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = TokenBucket(config.rate_limit_per_minute)
        self._circuit_breaker = CircuitBreaker()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def find_project_by_mis(self, mis_code: str) -> AnaptyxiProjectResponse:
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            # TODO(confirm against live API): exact endpoint path/params.
            response = await self._http.get("/projects", params={"misCode": mis_code})
            if response.status_code == 404:
                return response
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()

        if response.status_code == 404:
            raise ProjectNotFoundError(mis_code)

        response.raise_for_status()
        return AnaptyxiProjectResponse(
            mis_code=mis_code,
            body=response.json(),
            raw_body=response.content,
            http_status=response.status_code,
        )

    async def find_projects_by_beneficiary_afm(self, afm: str) -> AnaptyxiBeneficiarySearchResponse:
        """Join hierarchy Level 2 support (§19.2) — search by beneficiary/
        contractor ΑΦΜ, returning every matching project (unlike
        `find_project_by_mis`'s single-record lookup). No results is a
        legitimate empty list, not a 404 — unlike a specific MIS code not
        existing, "this ΑΦΜ has no ΑΝΑΠΤΥΞΗ projects" is an ordinary
        outcome. Endpoint path/params are a best-effort guess — same
        discipline as `find_project_by_mis`."""
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            # TODO(confirm against live API): exact endpoint path/params.
            response = await self._http.get("/projects/search", params={"beneficiaryVatNumber": afm})
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
        results = body.get("results") or body.get("data") or []
        return AnaptyxiBeneficiarySearchResponse(
            afm=afm, results=results, raw_body=response.content, http_status=response.status_code
        )
