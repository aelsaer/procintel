"""`_maybe_process_decision_document` — the previously-missing caller that
wires `services/documents/pipeline.py::process_document()` into Διαύγεια
decision resolution (§23's documents pipeline had zero automatic callers
before this). Opt-in (`process_documents=False` by default), and a
document-processing failure must never propagate — the decision itself is
already fully linked/stored by the time this runs.
"""

import uuid

import services.ingestion.connectors.diavgeia.resolve as resolve_module
from services.ingestion.connectors.diavgeia.db_writer import DecisionIngestResult


class _DocumentLookupResult:
    def __init__(self, value=None):
        self.value = value

    def first(self):
        return self.value


class _DocumentLookupConnection:
    def __init__(self, value=None):
        self.value = value

    async def execute(self, statement):
        return _DocumentLookupResult(self.value)


async def test_does_nothing_when_process_documents_is_false(monkeypatch):
    calls = []

    async def _spy_process_document(conn, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(resolve_module, "process_document", _spy_process_document)

    result = DecisionIngestResult(source_record_id=uuid.uuid4(), act_id=uuid.uuid4(), document_url="https://example.test/a.pdf")
    await resolve_module._maybe_process_decision_document(conn=object(), result=result, process_documents=False)
    assert calls == []


async def test_does_nothing_when_there_is_no_document_url(monkeypatch):
    calls = []

    async def _spy_process_document(conn, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(resolve_module, "process_document", _spy_process_document)

    result = DecisionIngestResult(source_record_id=uuid.uuid4(), act_id=uuid.uuid4(), document_url=None)
    await resolve_module._maybe_process_decision_document(
        conn=_DocumentLookupConnection(),
        result=result,
        process_documents=True,
    )
    assert calls == []


async def test_calls_process_document_with_the_decision_url_and_act_id(monkeypatch):
    calls = []

    async def _spy_process_document(conn, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(resolve_module, "process_document", _spy_process_document)

    act_id = uuid.uuid4()
    result = DecisionIngestResult(source_record_id=uuid.uuid4(), act_id=act_id, document_url="https://example.test/a.pdf")
    await resolve_module._maybe_process_decision_document(
        conn=_DocumentLookupConnection(),
        result=result,
        process_documents=True,
    )
    assert len(calls) == 1
    assert calls[0]["url"] == "https://example.test/a.pdf"
    assert calls[0]["act_id"] == act_id
    assert calls[0]["document_type"] == "DIAVGEIA_DECISION_PDF"


async def test_skips_download_when_decision_document_already_exists(monkeypatch):
    calls = []

    async def _spy_process_document(conn, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(resolve_module, "process_document", _spy_process_document)
    result = DecisionIngestResult(
        source_record_id=uuid.uuid4(),
        act_id=uuid.uuid4(),
        document_url="https://example.test/a.pdf",
    )

    status = await resolve_module._maybe_process_decision_document(
        conn=_DocumentLookupConnection(value=object()),
        result=result,
        process_documents=True,
    )

    assert status == "EXISTING"
    assert calls == []


async def test_a_processing_failure_is_swallowed_not_raised(monkeypatch):
    async def _failing_process_document(conn, **kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(resolve_module, "process_document", _failing_process_document)

    result = DecisionIngestResult(source_record_id=uuid.uuid4(), act_id=uuid.uuid4(), document_url="https://example.test/a.pdf")
    # must not raise
    await resolve_module._maybe_process_decision_document(
        conn=_DocumentLookupConnection(),
        result=result,
        process_documents=True,
    )
