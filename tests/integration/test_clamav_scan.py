"""`ClamdAntivirusScanner` against a real `clamd` daemon. Skipped
automatically unless `CLAMD_HOST` or `CLAMD_SOCKET_PATH` is set — no
`clamd` was installed/reachable in the sandbox this was built in
(confirmed: not installed, no passwordless `sudo` to install it), so this
is expected to skip here. `tests/contract/test_clamav_protocol.py` already
proves the wire protocol itself works, against a fake in-process server;
this file is for confirming against the real ClamAV binary specifically —
in particular the EICAR test string, the antivirus industry's own
standard "this should always be detected as a virus" test file
(https://en.wikipedia.org/wiki/EICAR_test_file — not a real virus, safe to
use in a test).
"""

import os

import pytest

from services.documents.clamav import ClamdAntivirusScanner, ClamdConfig

CLAMD_HOST = os.environ.get("CLAMD_HOST")
CLAMD_SOCKET_PATH = os.environ.get("CLAMD_SOCKET_PATH")
pytestmark = pytest.mark.skipif(
    not CLAMD_HOST and not CLAMD_SOCKET_PATH,
    reason="CLAMD_HOST/CLAMD_SOCKET_PATH not set — see module docstring",
)

# The standard EICAR antivirus test string — every real antivirus engine,
# including ClamAV, is required by convention to flag this exact string.
EICAR_TEST_STRING = (
    rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


async def test_clean_payload_is_reported_clean():
    scanner = ClamdAntivirusScanner(ClamdConfig.from_env())
    result = await scanner.scan(b"this is an ordinary, harmless PDF-shaped payload")
    assert result.is_clean is True


async def test_eicar_test_string_is_detected():
    scanner = ClamdAntivirusScanner(ClamdConfig.from_env())
    result = await scanner.scan(EICAR_TEST_STRING)
    assert result.is_clean is False
    assert result.signature is not None
