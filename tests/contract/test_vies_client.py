"""Connector-level tests against mocked HTTP (respx) — no live VIES access
required or attempted. SOAP envelope/response parsing is a best-effort
guess (see services/ingestion/connectors/vies/client.py's module docstring);
these tests exercise the parser against synthetic SOAP-shaped XML, not a
captured real VIES response."""

import httpx
import pytest
import respx

from packages.source_clients.retry import TransientServerError
from services.ingestion.connectors.vies.client import ViesClient
from services.ingestion.connectors.vies.config import ViesConnectorConfig

BASE_URL = "https://vies.example.test"

VALID_RESPONSE_XML = """<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <checkVatResponse>
      <countryCode>DE</countryCode>
      <vatNumber>123456789</vatNumber>
      <valid>true</valid>
      <name>EXAMPLE TECH GMBH</name>
    </checkVatResponse>
  </soap:Body>
</soap:Envelope>"""

INVALID_RESPONSE_XML = VALID_RESPONSE_XML.replace("<valid>true</valid>", "<valid>false</valid>")
UNPARSEABLE_RESPONSE_XML = "<soap:Envelope><soap:Body>unexpected</soap:Body></soap:Envelope>"


def _config(**overrides) -> ViesConnectorConfig:
    return ViesConnectorConfig(
        base_url=BASE_URL,
        rate_limit_per_minute=6000,
        max_retry_attempts=overrides.pop("max_retry_attempts", 5),
        **overrides,
    )


@respx.mock
async def test_check_vat_parses_valid_true():
    route = respx.post(f"{BASE_URL}/checkVatService").mock(
        return_value=httpx.Response(200, content=VALID_RESPONSE_XML, headers={"Content-Type": "text/xml"})
    )

    client = ViesClient(_config())
    try:
        response = await client.check_vat(country_code="DE", vat_number="123456789")
    finally:
        await client.aclose()

    assert response.valid is True
    sent_body = route.calls[0].request.content.decode()
    assert "<urn:countryCode>DE</urn:countryCode>" in sent_body
    assert "<urn:vatNumber>123456789</urn:vatNumber>" in sent_body


@respx.mock
async def test_check_vat_parses_valid_false():
    respx.post(f"{BASE_URL}/checkVatService").mock(
        return_value=httpx.Response(200, content=INVALID_RESPONSE_XML, headers={"Content-Type": "text/xml"})
    )

    client = ViesClient(_config())
    try:
        response = await client.check_vat(country_code="DE", vat_number="123456789")
    finally:
        await client.aclose()

    assert response.valid is False


@respx.mock
async def test_check_vat_unparseable_response_is_none_not_false():
    respx.post(f"{BASE_URL}/checkVatService").mock(
        return_value=httpx.Response(200, content=UNPARSEABLE_RESPONSE_XML, headers={"Content-Type": "text/xml"})
    )

    client = ViesClient(_config())
    try:
        response = await client.check_vat(country_code="DE", vat_number="123456789")
    finally:
        await client.aclose()

    assert response.valid is None


@respx.mock
async def test_5xx_is_retried_then_raises_on_exhaustion():
    respx.post(f"{BASE_URL}/checkVatService").mock(return_value=httpx.Response(503))

    client = ViesClient(_config(max_retry_attempts=2))
    try:
        with pytest.raises(TransientServerError):
            await client.check_vat(country_code="DE", vat_number="123456789")
    finally:
        await client.aclose()
