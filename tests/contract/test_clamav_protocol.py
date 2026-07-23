"""`ClamdAntivirusScanner`'s wire protocol against a real TCP socket — a
small fake `clamd` server started in-process for this test (real sockets,
real async I/O, just not the real ClamAV binary), so the INSTREAM framing
(length-prefixed chunks, zero-length terminator) is genuinely exercised
end to end without needing a real `clamd` daemon installed. The
`CLAMD_HOST`-gated integration test is for confirming against an actual
ClamAV install; this one confirms the client speaks the protocol
correctly regardless.
"""

import asyncio

import pytest

from services.documents.clamav import ClamdAntivirusScanner, ClamdConfig


class _FakeClamdServer:
    """Reads one INSTREAM request (header + framed chunks + zero-length
    terminator) and replies with a pre-configured response, recording the
    reassembled payload it received for the test to assert on."""

    def __init__(self, response: bytes) -> None:
        self.response = response
        self.received_payload: bytes | None = None
        self._server: asyncio.AbstractServer | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await reader.readuntil(b"\x00")
        assert header == b"zINSTREAM\x00"

        chunks = []
        while True:
            length_bytes = await reader.readexactly(4)
            length = int.from_bytes(length_bytes, "big")
            if length == 0:
                break
            chunks.append(await reader.readexactly(length))
        self.received_payload = b"".join(chunks)

        writer.write(self.response)
        await writer.drain()
        writer.close()

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


async def test_scan_sends_correctly_framed_payload_and_parses_clean_response():
    fake_server = _FakeClamdServer(response=b"stream: OK\x00")
    port = await fake_server.start()
    try:
        scanner = ClamdAntivirusScanner(ClamdConfig(host="127.0.0.1", port=port))
        payload = b"%PDF-1.4 some document content" * 500  # bigger than one 8KB chunk, exercises multi-chunk framing
        result = await scanner.scan(payload)

        assert result.is_clean is True
        assert fake_server.received_payload == payload
    finally:
        await fake_server.stop()


async def test_scan_parses_a_found_response_as_not_clean():
    fake_server = _FakeClamdServer(response=b"stream: Eicar-Test-Signature FOUND\x00")
    port = await fake_server.start()
    try:
        scanner = ClamdAntivirusScanner(ClamdConfig(host="127.0.0.1", port=port))
        result = await scanner.scan(b"fake eicar payload")
        assert result.is_clean is False
        assert result.signature == "Eicar-Test-Signature"
    finally:
        await fake_server.stop()


async def test_scan_fails_closed_when_nothing_is_listening():
    # an ephemeral port with no server bound to it — connection should be refused
    scanner = ClamdAntivirusScanner(ClamdConfig(host="127.0.0.1", port=1, timeout_seconds=2.0))
    result = await scanner.scan(b"payload")
    assert result.is_clean is False
    assert "CLAMD_UNREACHABLE" in (result.signature or "")


def test_clamd_config_from_env_requires_host_or_socket(monkeypatch):
    monkeypatch.delenv("CLAMD_HOST", raising=False)
    monkeypatch.delenv("CLAMD_SOCKET_PATH", raising=False)
    with pytest.raises(RuntimeError):
        ClamdConfig.from_env()


def test_clamd_config_from_env_reads_host_and_port(monkeypatch):
    monkeypatch.setenv("CLAMD_HOST", "clamav.example.test")
    monkeypatch.setenv("CLAMD_PORT", "1234")
    config = ClamdConfig.from_env()
    assert config.host == "clamav.example.test"
    assert config.port == 1234
