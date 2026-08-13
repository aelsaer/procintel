"""Immutable raw-payload storage (description.txt §13).

`LocalFilesystemRawStore` is the dev-environment implementation, laid out to
mirror the `raw/<source>/<resource>/ingestion_date=.../<partition>/<sha>.json`
S3 convention from §13.1 so an S3-compatible RawStore can be swapped in later
without touching any caller.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from packages.object_storage import ObjectStore, configured_object_store, safe_object_key


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
        relative_path = safe_object_key(
            f"{source}/{resource}/ingestion_date={ingestion_date.isoformat()}/"
            f"{partition_key}/{content_sha256}.json"
        )
        file_path = (self._root / relative_path).resolve()
        file_path.relative_to(self._root.resolve())

        file_path.parent.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.write_bytes(payload)
        return RawObjectRef(
            payload_uri=str(file_path),
            content_sha256=content_sha256,
            size_bytes=len(payload),
        )


class ObjectRawStore:
    def __init__(self, store: ObjectStore) -> None:
        self._store = store

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
        key = safe_object_key(
            f"{source}/{resource}/ingestion_date={ingestion_date.isoformat()}/"
            f"{partition_key}/{content_sha256}.json"
        )
        uri = await self._store.put(key, payload, content_type="application/json")
        return RawObjectRef(
            payload_uri=uri,
            content_sha256=content_sha256,
            size_bytes=len(payload),
        )


def configured_raw_store(root: Path | str) -> RawStore:
    if os.environ.get("OBJECT_STORAGE_BACKEND", "local").casefold() == "local":
        return LocalFilesystemRawStore(root)
    return ObjectRawStore(
        configured_object_store(
            local_root=root,
            s3_prefix=os.environ.get("OBJECT_STORAGE_RAW_PREFIX", "raw"),
        )
    )
