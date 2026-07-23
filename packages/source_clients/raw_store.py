"""Immutable raw-payload storage (description.txt §13).

`LocalFilesystemRawStore` is the dev-environment implementation, laid out to
mirror the `raw/<source>/<resource>/ingestion_date=.../<partition>/<sha>.json`
S3 convention from §13.1 so an S3-compatible RawStore can be swapped in later
without touching any caller.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RawObjectRef:
    payload_uri: str
    content_sha256: str
    size_bytes: int


class RawStore(Protocol):
    async def put(
        self,
        *,
        source: str,
        resource: str,
        partition_key: str,
        payload: bytes,
        ingestion_date: date | None = None,
    ) -> RawObjectRef: ...


class LocalFilesystemRawStore:
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    async def put(
        self,
        *,
        source: str,
        resource: str,
        partition_key: str,
        payload: bytes,
        ingestion_date: date | None = None,
    ) -> RawObjectRef:
        ingestion_date = ingestion_date or datetime.now(timezone.utc).date()
        content_sha256 = hashlib.sha256(payload).hexdigest()
        directory = (
            self._root
            / source
            / resource
            / f"ingestion_date={ingestion_date.isoformat()}"
            / partition_key
        )
        file_path = directory / f"{content_sha256}.json"

        directory.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.write_bytes(payload)
        return RawObjectRef(
            payload_uri=str(file_path),
            content_sha256=content_sha256,
            size_bytes=len(payload),
        )
