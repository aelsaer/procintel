from datetime import date
import uuid

import pytest

import services.ingestion.connectors.ted.scheduled as scheduled_module
from services.ingestion.connectors.ted.db_writer import TedIngestResult, TedNoticeUpsertResult
from services.search_index.config import OpenSearchConfig


class _PartitionResult:
    pages_fetched = 1
    notices_seen = 1
    notices_ingested = 1
    notices_failed = 0


def _notice_result() -> TedIngestResult:
    notice = TedNoticeUpsertResult(
        act_id=uuid.uuid4(),
        is_new=True,
        buyer_entity_id=None,
        supplier_entity_id=None,
        supplier_country_code=None,
        supplier_vat=None,
        cpv_codes=[],
        publication_date=None,
        title="GIS services",
        amount=None,
    )
    return TedIngestResult(source_record_id=uuid.uuid4(), notice=notice)


@pytest.fixture(autouse=True)
def _stub_ted_env(monkeypatch):
    monkeypatch.setenv("TED_API_BASE_URL", "https://ted.example.test")


@pytest.fixture(autouse=True)
def _stub_process_matching(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(scheduled_module, "resolve_notice_process_link", _noop)


async def test_ted_indexes_upserted_notice_when_configured(monkeypatch):
    indexed = []

    async def _index(conn, client, config, act_id):
        indexed.append(act_id)

    async def _ingest(**kwargs):
        result = _notice_result()
        await kwargs["on_notice_upserted"](kwargs["conn"], result)
        return _PartitionResult()

    monkeypatch.setattr(scheduled_module, "index_single_act", _index)
    monkeypatch.setattr(scheduled_module, "ingest_ted_partition", _ingest)

    result = await scheduled_module.run_scheduled_window(
        object(),
        date(2026, 6, 1),
        date(2026, 6, 2),
        opensearch_config=OpenSearchConfig(base_url="https://search.example.test"),
    )

    assert len(indexed) == 1
    assert result["enrichment_succeeded"]["OPENSEARCH"] == 1


async def test_ted_skips_incremental_index_when_not_configured(monkeypatch):
    indexed = []

    async def _index(*args, **kwargs):
        indexed.append(True)

    async def _ingest(**kwargs):
        await kwargs["on_notice_upserted"](kwargs["conn"], _notice_result())
        return _PartitionResult()

    monkeypatch.setattr(scheduled_module, "index_single_act", _index)
    monkeypatch.setattr(scheduled_module, "ingest_ted_partition", _ingest)

    await scheduled_module.run_scheduled_window(
        object(),
        date(2026, 6, 1),
        date(2026, 6, 2),
    )

    assert indexed == []
