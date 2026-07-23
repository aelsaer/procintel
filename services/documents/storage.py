"""Original-document blob storage, content-addressed by SHA-256.

Deliberately separate from `packages/source_clients/raw_store.py`: that
store lays out raw *ingestion* payloads (JSON API responses) under a
`source/resource/ingestion_date=.../partition/` tree and always writes a
`.json` file. Documents are arbitrary binary blobs (PDFs today) referenced
by `documents.object_uri` — a flat content-addressed layout keyed on the
hash itself is the right shape here (matches the "s3://documents/..."
convention in the migration's own comment), and a repeat download of the
same file is a guaranteed no-op write rather than a new dated partition.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_EXTENSION_BY_MIME = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
}


@dataclass(frozen=True)
class DocumentBlobRef:
    object_uri: str
    sha256: str
    size_bytes: int


class DocumentBlobStore(Protocol):
    async def put(self, *, payload: bytes, mime_type: str | None) -> DocumentBlobRef: ...


class LocalFilesystemDocumentBlobStore:
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    async def put(self, *, payload: bytes, mime_type: str | None) -> DocumentBlobRef:
        sha256 = hashlib.sha256(payload).hexdigest()
        extension = _EXTENSION_BY_MIME.get(mime_type or "", ".bin")
        file_path = self._root / sha256[:2] / f"{sha256}{extension}"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.write_bytes(payload)
        return DocumentBlobRef(object_uri=str(file_path), sha256=sha256, size_bytes=len(payload))
