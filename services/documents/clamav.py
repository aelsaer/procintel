"""Real ClamAV integration via the `clamd` daemon's INSTREAM wire protocol
— description.txt §23.2's "antivirus scan" step, now backed by an actual
scanner rather than only `antivirus.py`'s `NoOpAntivirusScanner`.

No `clamd` daemon exists in the sandbox this was built in (checked:
`clamav`/`clamdscan` aren't installed, and there's no passwordless `sudo`
to install them) — this implements the real protocol against
`CLAMD_HOST`/`CLAMD_PORT` (TCP) or `CLAMD_SOCKET_PATH` (Unix socket)
regardless, gated exactly like every other "needs a real external service"
test in this codebase (`tests/integration/test_clamav_scan_db.py`, gated
on `CLAMD_HOST`/`CLAMD_SOCKET_PATH`, confirmed to skip cleanly here).

Protocol (ClamAV's own `clamdscan`/`clamd` documentation): send
`zINSTREAM\\0`, then the payload as a sequence of `<4-byte big-endian
length><chunk bytes>` frames terminated by a zero-length frame, then read
one line back — `"stream: OK"`, `"stream: <signature> FOUND"`, or
`"stream: <message> ERROR"`. A response clamd can't produce a clean
`OK` for (a malformed/unparseable response, a protocol-level `ERROR`, a
connection failure) is treated as **not clean** — fail-closed, the
correct default for a security scanner: an unreachable/broken scanner
must never be silently treated as "this file passed."
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from .antivirus import ScanResult

DEFAULT_CHUNK_SIZE = 8192


class ClamdError(Exception):
    pass


@dataclass(frozen=True)
class ClamdConfig:
    host: str | None = None
    port: int = 3310
    socket_path: str | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "ClamdConfig":
        host = os.environ.get("CLAMD_HOST")
        socket_path = os.environ.get("CLAMD_SOCKET_PATH")
        if not host and not socket_path:
            raise RuntimeError(
                "Neither CLAMD_HOST nor CLAMD_SOCKET_PATH is set — required to use ClamdAntivirusScanner. "
                "See services/documents/README.md."
            )
        return cls(host=host, port=int(os.environ.get("CLAMD_PORT", "3310")), socket_path=socket_path)


def parse_clamd_response(raw: bytes) -> ScanResult:
    text = raw.decode("utf-8", errors="replace").rstrip("\x00").strip()
    if text.endswith("OK"):
        return ScanResult(is_clean=True)
    if "FOUND" in text:
        # "stream: <signature name> FOUND" -> just the signature name
        signature = text.split(":", 1)[-1].rsplit("FOUND", 1)[0].strip()
        return ScanResult(is_clean=False, signature=signature or "FOUND")
    # ERROR, empty, or any other unrecognized shape — fail closed, see module docstring
    return ScanResult(is_clean=False, signature=text or "UNRECOGNIZED_CLAMD_RESPONSE")


class ClamdAntivirusScanner:
    def __init__(self, config: ClamdConfig) -> None:
        self._config = config

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._config.socket_path:
            return await asyncio.open_unix_connection(self._config.socket_path)
        return await asyncio.open_connection(self._config.host, self._config.port)

    async def scan(self, payload: bytes) -> ScanResult:
        try:
            reader, writer = await asyncio.wait_for(self._connect(), timeout=self._config.timeout_seconds)
        except (OSError, asyncio.TimeoutError) as exc:
            return ScanResult(is_clean=False, signature=f"CLAMD_UNREACHABLE: {exc}")

        try:
            writer.write(b"zINSTREAM\x00")
            for offset in range(0, len(payload), DEFAULT_CHUNK_SIZE):
                chunk = payload[offset : offset + DEFAULT_CHUNK_SIZE]
                writer.write(len(chunk).to_bytes(4, "big"))
                writer.write(chunk)
            writer.write((0).to_bytes(4, "big"))
            await writer.drain()

            try:
                raw = await asyncio.wait_for(reader.read(4096), timeout=self._config.timeout_seconds)
            except asyncio.TimeoutError:
                return ScanResult(is_clean=False, signature="CLAMD_TIMEOUT")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

        return parse_clamd_response(raw)
