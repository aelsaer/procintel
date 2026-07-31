"""HTTP client for the official ΑΝΑΠΤΥΞΗ ``GetData.ashx`` API.

The public service is query-string based rather than REST-shaped. Project
details expose the complete hierarchy (subprojects, bodies, geographic
allocations, indicators and files); project lists support exact code searches
for contractors and beneficiaries through ``searchField``.
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
class AnaptyxiSubprojectResponse:
    mis_code: str
    subproject_index: int
    body: dict[str, Any]
    raw_body: bytes
    http_status: int


@dataclass(frozen=True)
class AnaptyxiBeneficiarySearchResponse:
    afm: str
    results: list[dict[str, Any]]
    raw_body: bytes
    http_status: int


def _list_query_type(program_period: str) -> str:
    return "projects" if program_period == "ANAPTYXI_2007_2013" else "projects_v2"


def _result_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("Records", "results", "data", "projects", "rows"):
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    # Some deployments return a map keyed by the MIS code.
    return [value for value in body.values() if isinstance(value, dict)]


def _project_json_or_not_found(
    response: httpx.Response,
    identifier: str,
) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ProjectNotFoundError(identifier) from exc
    if not isinstance(body, dict) or not body:
        raise ProjectNotFoundError(identifier)
    return body


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

    async def _get(self, params: dict[str, str | int]) -> httpx.Response:
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            response = await self._http.get("/GetData.ashx", params=params)
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

    async def find_project_by_mis(self, mis_code: str) -> AnaptyxiProjectResponse:
        try:
            response = await self._get(
                {
                    "queryType": "projectDetails",
                    "queryArgument": mis_code,
                    "projectDetails": "all",
                    "outputFormat": "json",
                }
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProjectNotFoundError(mis_code) from exc
            raise
        body = _project_json_or_not_found(response, mis_code)
        if (
            not body.get("title")
            and not body.get("projectTitle")
        ):
            raise ProjectNotFoundError(mis_code)
        return AnaptyxiProjectResponse(
            mis_code=mis_code,
            body=body,
            raw_body=response.content,
            http_status=response.status_code,
        )

    async def hydrate_project_summary(
        self,
        summary: dict[str, Any],
    ) -> AnaptyxiProjectResponse:
        """Resolve a list/search row to the complete project hierarchy."""
        mis_code = str(
            summary.get("kodikos")
            or summary.get("misCode")
            or summary.get("mis_ops_code")
            or summary.get("projectCode")
            or ""
        ).strip()
        if not mis_code:
            raise ProjectNotFoundError("")
        return await self.find_project_by_mis(mis_code)

    async def find_subproject(
        self,
        mis_code: str,
        subproject_index: int,
    ) -> AnaptyxiSubprojectResponse:
        response = await self._get(
            {
                "queryType": "subProjectDetails",
                "queryArgument": mis_code,
                "queryFilter": subproject_index,
                "outputFormat": "json",
            }
        )
        body = _project_json_or_not_found(
            response,
            f"{mis_code}/{subproject_index}",
        )
        return AnaptyxiSubprojectResponse(
            mis_code=mis_code,
            subproject_index=subproject_index,
            body=body,
            raw_body=response.content,
            http_status=response.status_code,
        )

    async def _search_company_field(self, afm: str, search_field: int) -> httpx.Response:
        return await self._get(
            {
                "queryType": _list_query_type(self.program_period),
                "outputFormat": "json",
                "searchField": search_field,
                "searchValue": afm,
                "pagesize": 1000,
                "pagenum": 0,
                "replaceEnum": "TRUE",
            }
        )

    async def find_projects_by_beneficiary_afm(self, afm: str) -> AnaptyxiBeneficiarySearchResponse:
        """Search the official contractor and beneficiary code fields.

        ``searchField=4`` is contractor and ``searchField=6`` is beneficiary.
        The source treats a code search as exact, while names remain partial.
        """
        normalized_afm = "".join(character for character in afm if character.isdigit())
        responses = [
            await self._search_company_field(normalized_afm, 4),
            await self._search_company_field(normalized_afm, 6),
        ]
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for response in responses:
            for row in _result_rows(response.json()):
                identifier = str(
                    row.get("kodikos")
                    or row.get("misCode")
                    or row.get("mis_ops_code")
                    or row.get("projectCode")
                    or ""
                )
                dedupe_key = identifier or repr(sorted(row.items()))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                results.append(row)
        raw_body = b"\n".join(response.content for response in responses)
        return AnaptyxiBeneficiarySearchResponse(
            afm=normalized_afm,
            results=results,
            raw_body=raw_body,
            http_status=max(response.status_code for response in responses),
        )
