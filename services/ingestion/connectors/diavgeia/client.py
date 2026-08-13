"""HTTP client for the published Διαύγεια Open Data API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any
from defusedxml import ElementTree

import httpx

from packages.source_clients.rate_limit import RateLimiter, TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying
from packages.source_clients.shared_rate_limit import SharedSlidingWindowLimiter

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


class InvalidReferencePayloadError(RuntimeError):
    pass


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_scalar(value: str | None) -> str | bool | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.lower() == "true":
        return True
    if normalized.lower() == "false":
        return False
    return normalized


def _xml_element_value(element: ElementTree.Element) -> Any:
    children = list(element)
    if not children:
        return _xml_scalar(element.text)

    grouped: dict[str, list[Any]] = {}
    for child in children:
        grouped.setdefault(_xml_local_name(child.tag), []).append(
            _xml_element_value(child)
        )
    return {
        key: values if len(values) > 1 else values[0]
        for key, values in grouped.items()
    }


def _parse_reference_xml(raw_body: bytes, *, resource: str) -> dict[str, Any]:
    try:
        root = ElementTree.fromstring(raw_body)
    except ElementTree.ParseError as exc:
        raise InvalidReferencePayloadError(
            f"{resource} returned malformed XML"
        ) from exc

    root_name = _xml_local_name(root.tag)
    singular = {
        "organizations": "organization",
        "units": "unit",
        "signers": "signer",
    }.get(resource, resource)
    if root_name not in {resource, singular}:
        raise InvalidReferencePayloadError(
            f"{resource} returned unexpected XML root {root_name!r}"
        )

    if root_name == resource and resource != singular:
        return {
            resource: [
                _xml_element_value(child)
                for child in root
                if _xml_local_name(child.tag) == singular
            ]
        }

    value = _xml_element_value(root)
    return value if isinstance(value, dict) else {root_name: value}


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


@dataclass(frozen=True)
class DiavgeiaReferenceResponse:
    resource: str
    body: Any
    raw_body: bytes
    http_status: int


class DiavgeiaClient:
    def __init__(
        self,
        config: DiavgeiaConnectorConfig,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.request_timeout_seconds
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = rate_limiter or (
            SharedSlidingWindowLimiter(
                int(config.rate_limit_per_minute),
                config.rate_limit_state_path,
            )
            if config.rate_limit_state_path
            else TokenBucket(config.rate_limit_per_minute)
        )
        self._reference_cache: dict[
            tuple[str, tuple[tuple[str, str], ...]], DiavgeiaReferenceResponse
        ] = {}
        # Separate circuit breakers per capability — §17.3 explicitly
        # requires that general search never blocks DIRECT_ADA_FETCH (e.g.
        # during Διαύγεια maintenance windows where search is degraded but
        # direct fetch still works). A single shared breaker would let a
        # broken SEARCH endpoint trip and block direct fetch too.
        self._circuit_breaker = CircuitBreaker()
        self._search_circuit_breaker = CircuitBreaker()
        self._advanced_search_circuit_breaker = CircuitBreaker()
        self._reference_circuit_breaker = CircuitBreaker()
        self.capabilities: dict[str, CapabilityStatus] = dict(DEFAULT_CAPABILITIES)

    @property
    def request_rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

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
        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            await self._rate_limiter.acquire()
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

    async def fetch_decision_version(self, version_id: str) -> DiavgeiaDecisionResponse:
        """Retrieve a submitted or published decision version by versionId."""
        self._circuit_breaker.raise_if_open()
        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            await self._rate_limiter.acquire()
            response = await self._http.get(f"/decisions/v/{version_id}")
            if response.status_code == 404:
                return response
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._circuit_breaker.record_failure()
            self.capabilities["VERSION_LOG"] = CapabilityStatus.DEGRADED
            raise
        self._circuit_breaker.record_success()
        if response.status_code == 404:
            raise DecisionNotFoundError(version_id)
        response.raise_for_status()
        self.capabilities["VERSION_LOG"] = CapabilityStatus.AVAILABLE
        body = response.json()
        return DiavgeiaDecisionResponse(
            ada=str(body.get("ada") or ""),
            body=body,
            raw_body=response.content,
            http_status=response.status_code,
        )

    async def fetch_correction_history(
        self,
        ada: str,
        *,
        max_versions: int = 20,
    ) -> list[DiavgeiaDecisionResponse]:
        """Follow ``correctedVersionId`` references back to the original."""
        current = await self.fetch_decision_by_ada(ada)
        history = [current]
        seen = {str(current.body.get("versionId") or "")}
        corrected_version_id = current.body.get("correctedVersionId")
        while corrected_version_id and len(history) < max_versions:
            key = str(corrected_version_id)
            if key in seen:
                break
            seen.add(key)
            previous = await self.fetch_decision_version(key)
            history.append(previous)
            corrected_version_id = previous.body.get("correctedVersionId")
        self.capabilities["VERSION_LOG"] = CapabilityStatus.AVAILABLE
        return history

    async def _fetch_reference(
        self,
        path: str,
        *,
        resource: str,
        params: dict[str, str] | None = None,
        capability: str,
    ) -> DiavgeiaReferenceResponse:
        cache_key = (path, tuple(sorted((params or {}).items())))
        cached = self._reference_cache.get(cache_key)
        if cached is not None:
            return cached

        self._reference_circuit_breaker.raise_if_open()
        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            await self._rate_limiter.acquire()
            response = await self._http.get(path, params=params)
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._reference_circuit_breaker.record_failure()
            self.capabilities[capability] = CapabilityStatus.DEGRADED
            raise
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            content_type = response.headers.get("content-type", "unknown")
            if "xml" not in content_type.lower() and not response.content.lstrip().startswith(
                (b"<?xml", b"<")
            ):
                self._reference_circuit_breaker.record_failure()
                self.capabilities[capability] = CapabilityStatus.DEGRADED
                raise InvalidReferencePayloadError(
                    f"{resource} returned neither JSON nor XML "
                    f"(HTTP {response.status_code}, content-type={content_type})"
                )
            try:
                body = _parse_reference_xml(response.content, resource=resource)
            except InvalidReferencePayloadError:
                self._reference_circuit_breaker.record_failure()
                self.capabilities[capability] = CapabilityStatus.DEGRADED
                raise
        self._reference_circuit_breaker.record_success()
        self.capabilities[capability] = CapabilityStatus.AVAILABLE
        result = DiavgeiaReferenceResponse(
            resource=resource,
            body=body,
            raw_body=response.content,
            http_status=response.status_code,
        )
        self._reference_cache[cache_key] = result
        return result

    async def list_organizations(
        self,
        *,
        status: str = "active",
        category: str | None = None,
    ) -> DiavgeiaReferenceResponse:
        params = {"status": status}
        if category:
            params["category"] = category
        return await self._fetch_reference(
            "/organizations",
            resource="organizations",
            params=params,
            capability="ORGANIZATION_LOOKUP",
        )

    async def get_organization(self, organization_uid: str) -> DiavgeiaReferenceResponse:
        return await self._fetch_reference(
            f"/organizations/{organization_uid}",
            resource="organization",
            capability="ORGANIZATION_LOOKUP",
        )

    async def list_organization_units(
        self,
        organization_uid: str,
        *,
        status: str = "active",
    ) -> DiavgeiaReferenceResponse:
        return await self._fetch_reference(
            f"/organizations/{organization_uid}/units",
            resource="units",
            params={"status": status},
            capability="ORGANIZATION_LOOKUP",
        )

    async def get_unit(self, unit_uid: str) -> DiavgeiaReferenceResponse:
        return await self._fetch_reference(
            f"/units/{unit_uid}",
            resource="unit",
            capability="ORGANIZATION_LOOKUP",
        )

    async def list_organization_signers(
        self,
        organization_uid: str,
        *,
        status: str = "active",
    ) -> DiavgeiaReferenceResponse:
        return await self._fetch_reference(
            f"/organizations/{organization_uid}/signers",
            resource="signers",
            params={"status": status},
            capability="SIGNER_LOOKUP",
        )

    async def get_signer(self, signer_uid: str) -> DiavgeiaReferenceResponse:
        return await self._fetch_reference(
            f"/signers/{signer_uid}",
            resource="signer",
            capability="SIGNER_LOOKUP",
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
        fetch. Path, parameters and the `decisions`/`info` response envelope
        are validated against the live Open Data endpoint.
        """
        self._search_circuit_breaker.raise_if_open()
        params: dict[str, str] = {}
        if organization_query:
            params["org"] = organization_query
        if title_query:
            params["subject"] = title_query
        if date_from:
            params["from_date"] = date_from.isoformat()
        if date_to:
            params["to_date"] = date_to.isoformat()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            await self._rate_limiter.acquire()
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
        discipline as `search_decisions()`. The composite filters use the
        same validated Open Data search endpoint.
        """
        self._advanced_search_circuit_breaker.raise_if_open()
        params: dict[str, str] = {}
        if organization_query:
            params["org"] = organization_query
        if title_query:
            params["subject"] = title_query
        if date_from:
            params["from_date"] = date_from.isoformat()
        if date_to:
            params["to_date"] = date_to.isoformat()
        if decision_type:
            params["type"] = decision_type
        if protocol_number:
            params["protocol"] = protocol_number
        if unit_label:
            params["unit"] = unit_label

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            await self._rate_limiter.acquire()
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
