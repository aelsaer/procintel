"""Magic-byte MIME sniffing — no `python-magic`/libmagic dependency needed
for the small set of formats this pipeline deals with. Sniffing the actual
bytes (not trusting a server's `Content-Type` header or a URL's extension)
is the point: §23.2's "MIME validation" step exists specifically because
either of those can be wrong or forged.
"""

from __future__ import annotations

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"PK\x03\x04", "application/zip"),  # also the container format for docx/xlsx/xlsx-based OOXML
)


def sniff_mime_type(payload: bytes) -> str | None:
    for signature, mime_type in _SIGNATURES:
        if payload.startswith(signature):
            return mime_type
    return None
