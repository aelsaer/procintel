"""Secure document download (§23.2) against mocked HTTP (respx) — the
streaming size-cap enforcement in particular, which only a real streamed
response (not a pre-buffered one) can exercise meaningfully."""

import httpx
import pytest
import respx

from services.documents.config import DocumentPipelineConfig
from services.documents.download import DocumentTooLargeError, download_document

URL = "https://example.test/tender-doc.pdf"


@respx.mock
async def test_downloads_payload_under_the_size_limit():
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 fake pdf content"))
    result = await download_document(URL, config=DocumentPipelineConfig(max_file_size_bytes=1_000_000))
    assert result.payload == b"%PDF-1.4 fake pdf content"
    assert result.http_status == 200


@respx.mock
async def test_rejects_payload_over_the_size_limit_while_streaming():
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 1000))
    with pytest.raises(DocumentTooLargeError):
        await download_document(URL, config=DocumentPipelineConfig(max_file_size_bytes=100))


@respx.mock
async def test_raises_on_non_2xx_status():
    respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await download_document(URL, config=DocumentPipelineConfig())
