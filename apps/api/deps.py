"""Shared non-DB FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        yield client
