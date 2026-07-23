"""SOAP client for VIES `checkVat` (spec §3.9, §7.2).

Endpoint path and SOAP envelope shape are a best-effort guess —
description.txt confirms VIES exposes a WSDL `checkVat` operation but not
this repo's exact envelope/response parsing (no live sample available).
Isolated to `check_vat()` so fixing it against the real WSDL is a one-place
change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import CircuitBreaker, raise_for_retryable_status, retrying

from .config import ViesConnectorConfig

_SOAP_ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" \
xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
  <soapenv:Header/>
  <soapenv:Body>
    <urn:checkVat>
      <urn:countryCode>{country_code}</urn:countryCode>
      <urn:vatNumber>{vat_number}</urn:vatNumber>
    </urn:checkVat>
  </soapenv:Body>
</soapenv:Envelope>"""

_VALID_PATTERN = re.compile(r"<(?:\w+:)?valid>(true|false)</(?:\w+:)?valid>", re.IGNORECASE)


@dataclass(frozen=True)
class ViesCheckResponse:
    country_code: str
    vat_number: str
    valid: bool | None  # None if the response couldn't be parsed — genuinely unknown, not "invalid"
    raw_body: bytes
    http_status: int


class ViesClient:
    def __init__(
        self,
        config: ViesConnectorConfig,
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

    async def check_vat(self, *, country_code: str, vat_number: str) -> ViesCheckResponse:
        self._circuit_breaker.raise_if_open()
        await self._rate_limiter.acquire()

        envelope = _SOAP_ENVELOPE.format(country_code=country_code, vat_number=vat_number)

        @retrying(max_attempts=self._config.max_retry_attempts)
        async def _do_request() -> httpx.Response:
            # TODO(confirm against live WSDL): exact SOAP endpoint path/envelope.
            response = await self._http.post(
                "/checkVatService",
                content=envelope,
                headers={"Content-Type": "text/xml; charset=utf-8"},
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
        match = _VALID_PATTERN.search(response.text)
        valid = (match.group(1).lower() == "true") if match else None

        return ViesCheckResponse(
            country_code=country_code,
            vat_number=vat_number,
            valid=valid,
            raw_body=response.content,
            http_status=response.status_code,
        )
