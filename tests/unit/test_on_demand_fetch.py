import httpx
import pytest

from services.ingestion.connectors.khmdhs.client import KhmdhsResourcePage
from services.ingestion.on_demand import (
    IdentifierTarget,
    _fetch_khmdhs_adam,
    _not_found_scan_complete,
    classify_identifier,
)


def test_classify_adam_routes_to_khmdhs():
    target = classify_identifier(" 25SYMV012345678 ")

    assert target is not None
    assert target.scheme == "ADAM"
    assert target.source_system == "KHMDHS"
    assert target.normalized == "25SYMV012345678"


def test_classify_greek_ada_routes_to_diavgeia():
    target = classify_identifier("6ΙΖ07Λ7-ΕΨΒ")

    assert target is not None
    assert target.scheme == "ADA"
    assert target.source_system == "DIAVGEIA"
    assert target.normalized == "6ΙΖ07Λ7-ΕΨΒ"


def test_broad_text_is_not_fetchable():
    assert classify_identifier("υπηρεσίες καθαρισμού") is None


def test_incomplete_not_found_cache_is_retryable():
    assert _not_found_scan_complete({"windows_planned": 13, "windows_scanned": 1}) is False
    assert _not_found_scan_complete({"windows_planned": 13, "windows_scanned": 13}) is True


@pytest.mark.asyncio
async def test_khmdhs_fetch_continues_after_empty_window_404(monkeypatch, tmp_path):
    clients = []

    class FakeKhmdhsClient:
        def __init__(self, _config):
            self.calls = []
            clients.append(self)

        async def fetch_resource_page(self, *, resource, page, date_from, date_to, reference_number=None):
            self.calls.append((resource, page, date_from, date_to, reference_number))
            if len(self.calls) == 1:
                request = httpx.Request("POST", "https://example.test/khmdhs-opendata/notice")
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError("empty window", request=request, response=response)
            return KhmdhsResourcePage(
                resource=resource,
                records=[],
                is_last_page=True,
                raw_body=b"{}",
                http_status=200,
            )

        async def aclose(self):
            return None

    class FakeConnection:
        async def commit(self):
            return None

    monkeypatch.setattr("services.ingestion.on_demand.KhmdhsClient", FakeKhmdhsClient)

    outcome = await _fetch_khmdhs_adam(
        FakeConnection(),
        target=IdentifierTarget(
            raw="26PROC019308569",
            normalized="26PROC019308569",
            scheme="ADAM",
            source_system="KHMDHS",
        ),
        raw_root=str(tmp_path),
    )

    assert outcome.status == "NOT_FOUND"
    assert outcome.metadata is not None
    assert outcome.metadata["windows_scanned"] == outcome.metadata["windows_planned"]
    assert outcome.metadata["empty_windows"] == 1
    assert len(clients[0].calls) > 1
