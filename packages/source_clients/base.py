"""Shared connector contract every source connector implements.

Reproduces description.txt §34 exactly, so this stays the one place the
protocol is defined instead of drifting per-connector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol


@dataclass(frozen=True)
class SourcePartition:
    resource: str
    date_from: str | None = None
    date_to: str | None = None
    cursor: str | None = None


@dataclass(frozen=True)
class RawEnvelope:
    source: str
    resource: str
    source_native_id: str | None
    payload: bytes
    content_type: str
    fetched_at: str
    source_updated_at: str | None
    metadata: dict[str, Any]


class SourceConnector(Protocol):
    source_name: str

    async def healthcheck(self) -> dict[str, Any]: ...

    async def partitions(
        self,
        state: dict[str, Any],
    ) -> AsyncIterator[SourcePartition]: ...

    async def fetch(
        self,
        partition: SourcePartition,
    ) -> AsyncIterator[RawEnvelope]: ...

    async def normalize(
        self,
        envelope: RawEnvelope,
    ) -> list[dict[str, Any]]: ...
