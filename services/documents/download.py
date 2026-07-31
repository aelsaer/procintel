"""Secure document download (§23.2): size cap enforced while streaming
(never buffer an unbounded response before checking it) and a timeout.

Documents are fetched from whatever URL the source record carries. When
the URL belongs to a rate-limited provider, the discovering connector
passes its shared token bucket so API and attachment requests consume the
same quota.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from packages.source_clients.rate_limit import TokenBucket
from packages.source_clients.retry import raise_for_retryable_status, retrying

from .config import DocumentPipelineConfig


class DocumentTooLargeError(Exception):
    def __init__(self, url: str, limit_bytes: int) -> None:
        super().__init__(f"document at {url!r} exceeds the {limit_bytes}-byte size limit")
        self.url = url
        self.limit_bytes = limit_bytes


@dataclass(frozen=True)
class DownloadedDocument:
    url: str
    payload: bytes
    http_status: int
    content_type: str | None


async def download_document(
    url: str,
    *,
    config: DocumentPipelineConfig,
    http_client: httpx.AsyncClient | None = None,
    rate_limiter: TokenBucket | None = None,
) -> DownloadedDocument:
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=config.download_timeout_seconds)

    @retrying(max_attempts=config.max_download_attempts)
    async def _download() -> DownloadedDocument:
        if rate_limiter is not None:
            await rate_limiter.acquire()
        chunks: list[bytes] = []
        total = 0
        async with client.stream(
            "GET", url, timeout=config.download_timeout_seconds
        ) as response:
            raise_for_retryable_status(response)
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > config.max_file_size_bytes:
                    raise DocumentTooLargeError(url, config.max_file_size_bytes)
                chunks.append(chunk)
            return DownloadedDocument(
                url=url,
                payload=b"".join(chunks),
                http_status=response.status_code,
                content_type=response.headers.get("content-type"),
            )

    try:
        return await _download()
    finally:
        if owns_client:
            await client.aclose()
