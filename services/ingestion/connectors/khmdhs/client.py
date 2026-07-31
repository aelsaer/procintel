"""HTTP client for the ΚΗΜΔΗΣ paginated resources (spec §3.1, §16.1):
request, notice, auction, contract, payment. `fetch_adam_chain` (Phase B)
covers the sixth, non-paginated endpoint.

The official ΚΗΜΔΗΣ help page documents `dateFrom`/`dateTo`, an optional
`referenceNumber` filter, a `content` response list, and a 350/minute
ceiling. This client targets 210/min by default via `KhmdhsConnectorConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying

from .config import KhmdhsConnectorConfig

KhmdhsResource = Literal["request", "notice", "auction", "contract", "payment"]

ALL_RESOURCES: frozenset[str] = frozenset({"request", "notice", "auction", "contract", "payment"})
_VALID_RESOURCES = ALL_RESOURCES


@dataclass(frozen=True)
class KhmdhsResourcePage:
    resource: str
    records: list[dict]
    is_last_page: bool
    raw_body: bytes
    http_status: int


@dataclass(frozen=True)
class KhmdhsAdamChainResponse:
    reference_number: str
    body: dict[str, list[str]]
    raw_body: bytes
    http_status: int


class KhmdhsClient:
    def __init__(
        self,
        config: KhmdhsConnectorConfig,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.request_timeout_seconds
        )
        self._owns_http_client = http_client is None
        self._rate_limiter = rate_limiter or TokenBucket(
            config.rate_limit_per_minute
        )
        self._circuit_breaker = CircuitBreaker()

    @property
    def request_rate_limiter(self) -> TokenBucket:
        return self._rate_limiter

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def fetch_resource_page(
        self,
        *,
        resource: KhmdhsResource,
        page: int,
        date_from: date,
        date_to: date,
        reference_number: str | None = None,
    ) -> KhmdhsResourcePage:
        if resource not in _VALID_RESOURCES:
            raise ValueError(f"unknown KHMDHS resource: {resource!r}")

        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            body = {
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
            }
            if reference_number:
                body["referenceNumber"] = reference_number
            response = await self._http.post(
                f"/khmdhs-opendata/{resource}",
                params={"page": page},
                json=body,
                headers={"Accept": "application/json"},
            )
            raise_for_retryable_status(response)
            return response

        try:
            response = await _do_request()
        except Exception:
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()

        response.raise_for_status()  # non-retryable 4xx surfaces as a real error
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("KHMDHS resource response must be a JSON object")
        records = body.get("content")
        if records is None:
            records = body.get("data")
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError("KHMDHS resource response has no valid record list")
        last_marker = body.get("last")
        if last_marker is None:
            last_marker = body.get("isLastPage")
        if not isinstance(last_marker, bool):
            raise ValueError("KHMDHS resource response has no boolean last-page marker")

        return KhmdhsResourcePage(
            resource=resource,
            records=records,
            is_last_page=last_marker,
            raw_body=response.content,
            http_status=response.status_code,
        )

    async def fetch_contract_page(self, *, page: int, date_from: date, date_to: date) -> KhmdhsResourcePage:
        return await self.fetch_resource_page(resource="contract", page=page, date_from=date_from, date_to=date_to)

    async def fetch_request_page(self, *, page: int, date_from: date, date_to: date) -> KhmdhsResourcePage:
        return await self.fetch_resource_page(resource="request", page=page, date_from=date_from, date_to=date_to)

    async def fetch_notice_page(self, *, page: int, date_from: date, date_to: date) -> KhmdhsResourcePage:
        return await self.fetch_resource_page(resource="notice", page=page, date_from=date_from, date_to=date_to)

    async def fetch_auction_page(self, *, page: int, date_from: date, date_to: date) -> KhmdhsResourcePage:
        return await self.fetch_resource_page(resource="auction", page=page, date_from=date_from, date_to=date_to)

    async def fetch_payment_page(self, *, page: int, date_from: date, date_to: date) -> KhmdhsResourcePage:
        return await self.fetch_resource_page(resource="payment", page=page, date_from=date_from, date_to=date_to)

    async def fetch_adam_chain(self, reference_number: str) -> KhmdhsAdamChainResponse:
        """Fetch the official six-bucket lifecycle chain for one ΑΔΑΜ."""
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            response = await self._http.get(f"/khmdhs-opendata/adamChain/{reference_number}")
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
        if not isinstance(body, dict):
            raise ValueError("KHMDHS adamChain response must be a JSON object")
        expected = (
            "requests",
            "approvedRequests",
            "notices",
            "auctions",
            "contracts",
            "payments",
        )
        for bucket_name in expected:
            bucket = body.get(bucket_name, [])
            if not isinstance(bucket, list) or any(
                not isinstance(item, (str, dict)) for item in bucket
            ):
                raise ValueError(
                    f"KHMDHS adamChain bucket {bucket_name!r} must be a list"
                )
        return KhmdhsAdamChainResponse(
            reference_number=reference_number,
            body=body,
            raw_body=response.content,
            http_status=response.status_code,
        )
