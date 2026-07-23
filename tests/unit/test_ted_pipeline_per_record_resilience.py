"""`ingest_ted_partition`'s per-record isolation — same fix as
`connectors/khmdhs/pipeline.py`, applied here since TED's pipeline had the
identical "one bad notice aborts the whole page" shape."""

import uuid
from dataclasses import dataclass
from datetime import date

import services.ingestion.connectors.ted.pipeline as pipeline_module
from services.ingestion.connectors.ted.client import TedSearchPage
from services.ingestion.connectors.ted.db_writer import TedIngestResult, TedNoticeUpsertResult

SOME_DATE = date(2026, 6, 1)


class _FakeRawStore:
    async def put(self, *, source, resource, partition_key, payload):
        from packages.source_clients.raw_store import RawObjectRef

        return RawObjectRef(payload_uri=f"mem://{partition_key}", content_sha256=partition_key, size_bytes=len(payload))


class _FakeClient:
    def __init__(self, pages: list[TedSearchPage]) -> None:
        self._pages = pages

    async def search_notices(self, *, country, date_from, date_to, page):
        return self._pages[page]


@dataclass(frozen=True)
class _FakeConn:
    async def commit(self):
        return None


def _notice(notice_id: str) -> dict:
    return {"noticeId": notice_id, "title": f"Test {notice_id}"}


def _page(notices: list[dict], *, is_last_page: bool) -> TedSearchPage:
    return TedSearchPage(notices=notices, is_last_page=is_last_page, raw_body=b"{}", http_status=200)


def _ingest_result() -> TedIngestResult:
    return TedIngestResult(
        source_record_id=uuid.uuid4(),
        notice=TedNoticeUpsertResult(
            act_id=uuid.uuid4(),
            is_new=True,
            supplier_entity_id=None,
            supplier_country_code=None,
            supplier_vat=None,
            buyer_entity_id=None,
            cpv_codes=[],
            publication_date=SOME_DATE,
        ),
    )


async def test_one_malformed_notice_does_not_lose_the_rest_of_the_page(monkeypatch):
    notices = [_notice("N1"), _notice("BAD"), _notice("N3")]
    client = _FakeClient([_page(notices, is_last_page=True)])

    async def _fake_ingest_notice_record(conn, *, ted_notice_id, **kwargs):
        if ted_notice_id == "BAD":
            raise ValueError("simulated normalization error")
        return _ingest_result()

    monkeypatch.setattr(pipeline_module, "ingest_notice_record", _fake_ingest_notice_record)

    result = await pipeline_module.ingest_ted_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(),
        country="GR",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
    )

    assert result.notices_seen == 3
    assert result.notices_ingested == 2
    assert result.notices_failed == 1
    assert result.failed_notices[0]["notice_id"] == "BAD"
    assert result.failed_notices[0]["stage"] == "ingest"


async def test_a_failing_hook_does_not_lose_the_already_ingested_notice(monkeypatch):
    notices = [_notice("N1"), _notice("N2")]
    client = _FakeClient([_page(notices, is_last_page=True)])

    async def _fake_ingest_notice_record(conn, *, ted_notice_id, **kwargs):
        return _ingest_result()

    async def _flaky_hook(conn, ingest_result):
        raise RuntimeError("simulated VIES timeout")

    monkeypatch.setattr(pipeline_module, "ingest_notice_record", _fake_ingest_notice_record)

    result = await pipeline_module.ingest_ted_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(),
        country="GR",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
        on_notice_upserted=_flaky_hook,
    )

    assert result.notices_ingested == 2  # both survive despite the hook always failing
    assert result.notices_failed == 2
    assert all(f["stage"] == "on_notice_upserted" for f in result.failed_notices)


async def test_daily_mode_retries_enrichment_for_unchanged_notices(monkeypatch):
    client = _FakeClient([_page([_notice("N1")], is_last_page=True)])

    async def _fake_dedup(conn, *, ted_notice_id, **kwargs):
        return TedIngestResult(source_record_id=None, notice=None)

    async def _fake_existing_context(conn, *, ted_notice_id, raw_body):
        return TedIngestResult(
            source_record_id=None,
            notice=TedNoticeUpsertResult(
                act_id=uuid.uuid4(),
                is_new=False,
                supplier_entity_id=None,
                supplier_country_code=None,
                supplier_vat=None,
                buyer_entity_id=None,
                cpv_codes=[],
                publication_date=SOME_DATE,
            ),
        )

    hook_calls = []

    async def _hook(conn, ingest_result):
        hook_calls.append(ingest_result.notice.act_id)

    monkeypatch.setattr(pipeline_module, "ingest_notice_record", _fake_dedup)
    monkeypatch.setattr(pipeline_module, "load_existing_notice_context", _fake_existing_context)

    result = await pipeline_module.ingest_ted_partition(
        client=client,
        raw_store=_FakeRawStore(),
        conn=_FakeConn(),
        country="GR",
        date_from=SOME_DATE,
        date_to=SOME_DATE,
        on_notice_upserted=_hook,
        enrich_deduplicated=True,
    )

    assert result.notices_ingested == 0
    assert result.notices_failed == 0
    assert len(hook_calls) == 1
