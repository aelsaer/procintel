import pytest

from services.documents.antivirus import NoOpAntivirusScanner, configured_antivirus_scanner
from services.documents.clamav import ClamdAntivirusScanner


def test_antivirus_factory_uses_noop_only_outside_production(monkeypatch):
    monkeypatch.delenv("CLAMD_HOST", raising=False)
    monkeypatch.delenv("CLAMD_SOCKET_PATH", raising=False)
    monkeypatch.setenv("PROCINTEL_ENV", "development")
    assert isinstance(configured_antivirus_scanner(), NoOpAntivirusScanner)


def test_antivirus_factory_requires_scanner_in_production(monkeypatch):
    monkeypatch.delenv("CLAMD_HOST", raising=False)
    monkeypatch.delenv("CLAMD_SOCKET_PATH", raising=False)
    monkeypatch.setenv("PROCINTEL_ENV", "production")
    with pytest.raises(RuntimeError, match="CLAMD_HOST"):
        configured_antivirus_scanner()


def test_antivirus_factory_uses_clamd_when_configured(monkeypatch):
    monkeypatch.setenv("CLAMD_HOST", "clamav")
    monkeypatch.delenv("CLAMD_SOCKET_PATH", raising=False)
    assert isinstance(configured_antivirus_scanner(), ClamdAntivirusScanner)
