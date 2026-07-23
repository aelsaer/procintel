"""`ingest_khmdhs_partition`'s per-record isolation — a real production
gap: 34,299 of 34,300 contract-shaped acts in a live June-2026 backfill
stayed adamChain-discovered placeholders because a single malformed
record's `procedure_type` field aborted the *entire* page/partition,
discarding every other valid record already processed in it. Confirms one
bad record (or one flaky `on_ingest_result` enrichment hook, e.g. a live
adamChain HTTP hiccup) no longer takes the rest of the page down with it.
"""

import uuid
from dataclasses import dataclass
from datetime import date

import services.ingestion.connectors.khmdhs.pipeline as pipeline_module
from services.ingestion.connectors.khmdhs.client import KhmdhsResourcePage
from services.ingestion.connectors.khmdhs.db_writer import ActUpsertResult, IngestResult

SOME_DATE = date(2026, 6, 1)


class _FakeRawStore:
    async def put(self, *, source, resource, partition_key, payload):
        from packages.source_clients.raw_store import RawObjectRef

        return RawObjectRef(payload_uri=f"mem://{partition_key}", content_sha256=partition_key, size_bytes=len(payload))


class _FakeClient:
    def __init__(self, pages: list[KhmdhsResourcePage]) -> None:
        self._pages = pages
        self.calls = 0

    async def fetch_resource_page(self, *, resource, page, date_from, date_to):
        result = self._pages[page]
        self.calls += 1
        return result


@dataclass(frozen=True)
class _FakeConn:
    """`ingest_khmdhs_record`/`on_ingest_result` are monkeypatched below, so
    `conn` is never actually touched — only `commit()` needs to exist."""

    committed: list[bool]

    async def commit(self):
        self.committed.append(True)


def _record(adam: str) -> dict:
    return {"referenceNumber": adam, "title": f"Test {adam}"}


def _page(records: list[dict], *, is_last_page: bool) -> KhmdhsResourcePage:
    return KhmdhsResourcePage(resource="contract", records=records, is_last_page=is_last_page, raw_body=b"{}", http_status=200)


def _ingest_result(adam: str) -> IngestResult:
    return IngestResult(
        source_record_id=uuid.uuid4(),
        adam_normalized=adam,
        act_upsert=ActUpsertResult(act_id=uuid.uuid4(), act_type="CONTRACT", is_new=True),
    )


async def test_one_malformed_record_does_not_lose_the_rest_of_the_page(monkeypatch):
    records = [_record("A1"), _record("BAD"), _record("A3")]
    client = _FakeClient([_page(records, is_last_page=True)])

    async def _fake_ingest_khmdhs_record(conn, *, resource, raw_record, **kwargs):
        if raw_record["referenceNumber"] == "BAD":
            raise ValueError("simulated procedure_type validation error")
        return _ingest_result(raw_record["referenceNumber"])

    monkeypatch.setattr(pipeline_module, "ingest_khmdhs_record", _fake_ingest_khmdhs_record)

    result = await pipeline_module.ingest_khmdhs_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(committed=[]),
        resource="contract",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
    )

    assert result.records_seen == 3
    assert result.records_ingested == 2  # A1 and A3 survived
    assert result.records_failed == 1
    assert result.failed_records[0]["adam"] == "BAD"
    assert result.failed_records[0]["stage"] == "ingest"
    assert "simulated procedure_type validation error" in result.failed_records[0]["error"]


async def test_a_failing_enrichment_hook_does_not_lose_the_already_ingested_record_or_the_rest_of_the_page(monkeypatch):
    records = [_record("A1"), _record("A2")]
    client = _FakeClient([_page(records, is_last_page=True)])

    async def _fake_ingest_khmdhs_record(conn, *, resource, raw_record, **kwargs):
        return _ingest_result(raw_record["referenceNumber"])

    hook_calls = []

    async def _flaky_hook(conn, resource, ingest_result):
        hook_calls.append(ingest_result.adam_normalized)
        if ingest_result.adam_normalized == "A1":
            raise RuntimeError("simulated adamChain HTTP timeout")

    monkeypatch.setattr(pipeline_module, "ingest_khmdhs_record", _fake_ingest_khmdhs_record)

    result = await pipeline_module.ingest_khmdhs_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(committed=[]),
        resource="contract",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
        on_ingest_result=_flaky_hook,
    )

    # both records were ingested (the hook failure doesn't retroactively
    # un-ingest A1), the hook ran for both, and only the hook failure counts
    assert result.records_ingested == 2
    assert hook_calls == ["A1", "A2"]
    assert result.records_failed == 1
    assert result.failed_records[0]["stage"] == "on_ingest_result"
    assert result.failed_records[0]["adam"] == "A1"


async def test_failures_beyond_the_bounded_sample_still_count_but_are_not_all_recorded(monkeypatch):
    records = [_record(f"BAD{i}") for i in range(60)]
    client = _FakeClient([_page(records, is_last_page=True)])

    async def _always_fails(conn, *, resource, raw_record, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(pipeline_module, "ingest_khmdhs_record", _always_fails)

    result = await pipeline_module.ingest_khmdhs_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(committed=[]),
        resource="contract",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
    )

    assert result.records_failed == 60
    assert len(result.failed_records) == pipeline_module._MAX_RECORDED_FAILURES


async def test_all_records_across_multiple_pages_succeed_when_nothing_fails(monkeypatch):
    page_one = _page([_record("A1"), _record("A2")], is_last_page=False)
    page_two = _page([_record("A3")], is_last_page=True)
    client = _FakeClient([page_one, page_two])

    async def _fake_ingest_khmdhs_record(conn, *, resource, raw_record, **kwargs):
        return _ingest_result(raw_record["referenceNumber"])

    monkeypatch.setattr(pipeline_module, "ingest_khmdhs_record", _fake_ingest_khmdhs_record)

    result = await pipeline_module.ingest_khmdhs_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(committed=[]),
        resource="contract",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
    )
    assert result.pages_fetched == 2
    assert result.records_seen == 3
    assert result.records_ingested == 3
    assert result.records_failed == 0
    assert result.failed_records == []


async def test_daily_mode_retries_enrichment_for_unchanged_records(monkeypatch):
    client = _FakeClient([_page([_record("A1")], is_last_page=True)])

    async def _fake_dedup(conn, *, resource, raw_record, **kwargs):
        return IngestResult(
            source_record_id=None,
            adam_normalized=raw_record["referenceNumber"],
            act_upsert=None,
        )

    async def _fake_existing_context(conn, *, resource, raw_record):
        return IngestResult(
            source_record_id=None,
            adam_normalized=raw_record["referenceNumber"],
            act_upsert=ActUpsertResult(act_id=uuid.uuid4(), act_type="CONTRACT", is_new=False),
        )

    hook_calls = []

    async def _hook(conn, resource, ingest_result):
        hook_calls.append(ingest_result.adam_normalized)

    monkeypatch.setattr(pipeline_module, "ingest_khmdhs_record", _fake_dedup)
    monkeypatch.setattr(pipeline_module, "load_existing_act_context", _fake_existing_context)

    result = await pipeline_module.ingest_khmdhs_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(committed=[]),
        resource="contract",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
        on_ingest_result=_hook,
        enrich_deduplicated=True,
    )

    assert result.records_ingested == 0
    assert result.records_failed == 0
    assert hook_calls == ["A1"]
