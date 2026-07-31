"""Antivirus scanning abstraction — description.txt §23.2 ("antivirus
scan" is a named step of the pipeline, before the original is stored).

Mirrors the `DeliveryChannel`/`RawStore` Protocol-plus-default-implementation
pattern used elsewhere in this codebase. `NoOpAntivirusScanner` always
reports clean — a safe default with no infra dependency, useful for tests
and for exercising the rest of the pipeline without a scanner available.
`clamav.py::ClamdAntivirusScanner` is a real implementation (the `clamd`
daemon's wire protocol) a pipeline caller can pass in instead — see that
module for why it isn't the default (no `clamd` daemon reachable in the
sandbox this was built in) and how it's gated (`CLAMD_HOST`/
`CLAMD_SOCKET_PATH`).
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol


@dataclass(frozen=True)
class ScanResult:
    is_clean: bool
    signature: str | None = None  # populated only when is_clean is False


class AntivirusScanner(Protocol):
    async def scan(self, payload: bytes) -> ScanResult: ...


class NoOpAntivirusScanner:
    """Always reports clean. NOT wired to any real scan engine — see module
    docstring. Using this in production would mean §23.2's antivirus step
    is a no-op; it exists so every other pipeline step is real and tested
    before that infra decision is made."""

    async def scan(self, payload: bytes) -> ScanResult:
        return ScanResult(is_clean=True)


def configured_antivirus_scanner() -> AntivirusScanner:
    if os.environ.get("CLAMD_HOST") or os.environ.get("CLAMD_SOCKET_PATH"):
        from .clamav import ClamdAntivirusScanner, ClamdConfig

        return ClamdAntivirusScanner(ClamdConfig.from_env())
    if os.environ.get("PROCINTEL_ENV", "development").lower() == "production":
        raise RuntimeError("CLAMD_HOST or CLAMD_SOCKET_PATH is required in production")
    return NoOpAntivirusScanner()
