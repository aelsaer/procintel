"""Secure document download (§23.2) against mocked HTTP (respx) — the
streaming size-cap enforcement in particular, which only a real streamed
response (not a pre-buffered one) can exercise meaningfully."""

import httpx
import pytest
import respx

from packages.source_clients.rate_limit import TokenBucket
from services.documents.config import DocumentPipelineConfig
from services.documents.download import DocumentTooLargeError, download_document

URL = "https://example.test/tender-doc.pdf"


@respx.mock
async def test_downloads_payload_under_the_size_limit():
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 fake pdf content"))
    result = await download_document(
        URL,
        config=DocumentPipelineConfig(max_file_size_bytes=1_000_000, allow_test_hosts=True),
    )
    assert result.payload == b"%PDF-1.4 fake pdf content"
    assert result.http_status == 200


@respx.mock
async def test_rejects_payload_over_the_size_limit_while_streaming():
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 1000))
    with pytest.raises(DocumentTooLargeError):
        await download_document(
            URL,
            config=DocumentPipelineConfig(max_file_size_bytes=100, allow_test_hosts=True),
        )


@respx.mock
async def test_raises_on_non_2xx_status():
    respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await download_document(URL, config=DocumentPipelineConfig(allow_test_hosts=True))


@respx.mock
async def test_retries_rate_limited_download_with_shared_provider_quota():
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, content=b"%PDF-1.4 retried"),
    ]
    limiter = TokenBucket(rate_per_minute=6000, burst=2)

    result = await download_document(
        URL,
        config=DocumentPipelineConfig(max_download_attempts=2, allow_test_hosts=True),
        rate_limiter=limiter,
    )

    assert result.payload == b"%PDF-1.4 retried"
    assert route.call_count == 2


@respx.mock
async def test_revalidates_each_redirect_destination():
    respx.get(URL).mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/latest/meta-data"},
        )
    )

    with pytest.raises(ValueError, match="non-public network address"):
        await download_document(
            URL,
            config=DocumentPipelineConfig(allow_test_hosts=True),
        )
