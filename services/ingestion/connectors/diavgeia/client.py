"""HTTP client for Διαύγεια direct-ΑΔΑ decision fetch (spec §3.2, §17).

Primary path only: DIRECT_ADA_FETCH. General/advanced search, organization
lookup, signer lookup and version log are represented as capabilities with
status UNKNOWN (not implemented) rather than silently absent — §17.3's
degraded-mode model says direct fetch must never be blocked by search being
unavailable, and callers should be able to check what's actually usable.

The official Open Data help page documents production
`https://diavgeia.gov.gr/opendata`, `GET /decisions/:ada`, and `GET /search`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying

from .config import DiavgeiaConnectorConfig


class CapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


DEFAULT_CAPABILITIES: dict[str, CapabilityStatus] = {
    "DIRECT_ADA_FETCH": CapabilityStatus.UNKNOWN,
    "SEARCH": CapabilityStatus.UNKNOWN,
    "ADVANCED_SEARCH": CapabilityStatus.UNKNOWN,
    "ORGANIZATION_LOOKUP": CapabilityStatus.UNKNOWN,
    "SIGNER_LOOKUP": CapabilityStatus.UNKNOWN,
    "VERSION_LOG": CapabilityStatus.UNKNOWN,
}


class DecisionNotFoundError(Exception):
    def __init__(self, ada: str) -> None:
        self.ada = ada
        super().__init__(f"no Διαύγεια decision found for ΑΔΑ {ada}")


@dataclass(frozen=True)
class DiavgeiaDecisionResponse:
    ada: str
    body: dict[str, Any]
    raw_body: bytes
    http_status: int


@dataclass(frozen=True)
class DiavgeiaSearchResponse:
    results: list[dict[str, Any]]
    raw_body: bytes
    http_status: int


class DiavgeiaClient:
    def __init__(
        self,
        config: DiavgeiaConnectorConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.request_timeout_seconds
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = TokenBucket(config.rate_limit_per_minute)
        # Separate circuit breakers per capability — §17.3 explicitly
        # requires that general search never blocks DIRECT_ADA_FETCH (e.g.
        # during Διαύγεια maintenance windows where search is degraded but
        # direct fetch still works). A single shared breaker would let a
        # broken SEARCH endpoint trip and block direct fetch too.
        self._circuit_breaker = CircuitBreaker()
        self._search_circuit_breaker = CircuitBreaker()
        self._advanced_search_circuit_breaker = CircuitBreaker()
        self.capabilities: dict[str, CapabilityStatus] = dict(DEFAULT_CAPABILITIES)

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def fetch_decision_by_ada(self, ada: str) -> DiavgeiaDecisionResponse:
        """DIRECT_ADA_FETCH — the primary, always-attempted capability
        (§17.3). Never gated on SEARCH's status. Raises
        DecisionNotFoundError on a 404 (a valid, expected outcome — not
        every ΑΔΑ referenced elsewhere resolves to a published decision),
        not a retryable failure."""
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            response = await self._http.get(f"/decisions/{ada}")
            if response.status_code == 404:
                return response  # not retryable, not a failure — handled below
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._circuit_breaker.record_failure()
            self.capabilities["DIRECT_ADA_FETCH"] = CapabilityStatus.DEGRADED
            raise
        self._circuit_breaker.record_success()

        if response.status_code == 404:
            self.capabilities["DIRECT_ADA_FETCH"] = CapabilityStatus.AVAILABLE
            raise DecisionNotFoundError(ada)

        response.raise_for_status()
        self.capabilities["DIRECT_ADA_FETCH"] = CapabilityStatus.AVAILABLE

        return DiavgeiaDecisionResponse(
            ada=ada,
            body=response.json(),
            raw_body=response.content,
            http_status=response.status_code,
        )

    async def search_decisions(
        self,
        *,
        organization_query: str | None = None,
        title_query: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> DiavgeiaSearchResponse:
        """SEARCH capability (§17.3) — general search by organization/title/
        date window, used by `resolve.py`'s fallback linkage (§17.4:
        "search by title or organization") when DIRECT_ADA_FETCH finds
        nothing for a referenced ΑΔΑ. Tracked via its own circuit breaker
        (see `__init__`) so a degraded SEARCH endpoint never blocks direct
        fetch. Endpoint path/params are a best-effort guess — description.txt
        confirms the capability exists (§17.3) but gives no request shape.
        """
        self._search_circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        params: dict[str, str] = {}
        if organization_query:
            params["org"] = organization_query
        if title_query:
            params["subject"] = title_query
        if date_from:
            params["from_issue_date"] = date_from.isoformat()
        if date_to:
            params["to_issue_date"] = date_to.isoformat()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            response = await self._http.get("/search", params=params)
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._search_circuit_breaker.record_failure()
            self.capabilities["SEARCH"] = CapabilityStatus.DEGRADED
            raise
        self._search_circuit_breaker.record_success()

        response.raise_for_status()
        self.capabilities["SEARCH"] = CapabilityStatus.AVAILABLE
        body = response.json()
        results = body.get("results") or body.get("decisions") or []
        return DiavgeiaSearchResponse(results=results, raw_body=response.content, http_status=response.status_code)

    async def search_decisions_advanced(
        self,
        *,
        organization_query: str | None = None,
        title_query: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        decision_type: str | None = None,
        protocol_number: str | None = None,
        unit_label: str | None = None,
    ) -> DiavgeiaSearchResponse:
        """ADVANCED_SEARCH capability (§17.3) — composite search adding
        decision-type/protocol-number/organizational-unit filters on top of
        SEARCH's organization/title/date window. Used by `resolve.py` as a
        disambiguation narrower when a basic SEARCH call returns zero or
        multiple candidates and the caller has one of these extra filters
        available — not a replacement for SEARCH, a follow-up query with
        more constraints. Tracked via its own circuit breaker so a degraded
        ADVANCED_SEARCH never blocks SEARCH or direct fetch, same §17.3
        discipline as `search_decisions()`. Endpoint path/params are a
        best-effort guess — description.txt confirms the capability exists
        but gives no request shape.
        """
        self._advanced_search_circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        params: dict[str, str] = {}
        if organization_query:
            params["org"] = organization_query
        if title_query:
            params["subject"] = title_query
        if date_from:
            params["from_issue_date"] = date_from.isoformat()
        if date_to:
            params["to_issue_date"] = date_to.isoformat()
        if decision_type:
            params["type"] = decision_type
        if protocol_number:
            params["protocol"] = protocol_number
        if unit_label:
            params["unit"] = unit_label

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            response = await self._http.get("/search", params=params)
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._advanced_search_circuit_breaker.record_failure()
            self.capabilities["ADVANCED_SEARCH"] = CapabilityStatus.DEGRADED
            raise
        self._advanced_search_circuit_breaker.record_success()

        response.raise_for_status()
        self.capabilities["ADVANCED_SEARCH"] = CapabilityStatus.AVAILABLE
        body = response.json()
        results = body.get("results") or body.get("decisions") or []
        return DiavgeiaSearchResponse(results=results, raw_body=response.content, http_status=response.status_code)
