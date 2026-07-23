"""`run_scheduled_window`'s optional OpenSearch incremental-indexing hook —
confirms `index_single_act` is called when `opensearch_config` is passed,
and is a no-op when it isn't, without touching a real ΚΗΜΔΗΣ API, DB, or
OpenSearch cluster: `ingest_khmdhs_partition` is monkeypatched to a fake
that just invokes the `on_ingest_result` callback directly with a
synthetic result, and `resolve_adam_chain_for_act`/`evaluate_and_fire` are
monkeypatched to no-ops so only the indexing hook itself is under test.
"""

import uuid
from dataclasses import dataclass, field

import pytest

import services.ingestion.connectors.khmdhs.scheduled as scheduled_module
from services.ingestion.connectors.khmdhs.db_writer import ActUpsertResult, IngestResult
from services.search_index.config import OpenSearchConfig


@dataclass(frozen=True)
class _FakePartitionResult:
    pages_fetched: int = 1
    records_seen: int = 1
    records_ingested: int = 1
    records_failed: int = 0


def _make_ingest_result() -> IngestResult:
    return IngestResult(
        source_record_id=uuid.uuid4(),
        adam_normalized="25SYMV000000001",
        act_upsert=ActUpsertResult(act_id=uuid.uuid4(), act_type="CONTRACT", is_new=True),
    )


@pytest.fixture(autouse=True)
def _stub_khmdhs_env(monkeypatch):
    monkeypatch.setenv("KHMDHS_API_BASE_URL", "https://khmdhs.example.test")


@pytest.fixture(autouse=True)
def _stub_enrichment_hooks(monkeypatch):
    async def _noop_resolve_adam_chain(*args, **kwargs):
        return None

    async def _noop_evaluate_and_fire(*args, **kwargs):
        return 0

    monkeypatch.setattr(scheduled_module, "resolve_adam_chain_for_act", _noop_resolve_adam_chain)
    monkeypatch.setattr(scheduled_module, "evaluate_and_fire", _noop_evaluate_and_fire)


async def test_indexes_every_upserted_act_when_opensearch_configured(monkeypatch):
    indexed_act_ids = []

    async def _fake_index_single_act(conn, http_client, config, act_id):
        indexed_act_ids.append(act_id)

    monkeypatch.setattr(scheduled_module, "index_single_act", _fake_index_single_act)

    async def _fake_ingest_khmdhs_partition(
        *, client, raw_store, conn, resource, date_from, date_to, on_ingest_result, enrich_deduplicated
    ):
        assert enrich_deduplicated is True
        result = _make_ingest_result()
        await on_ingest_result(conn, resource, result)
        return _FakePartitionResult()

    monkeypatch.setattr(scheduled_module, "ingest_khmdhs_partition", _fake_ingest_khmdhs_partition)

    from datetime import date

    await scheduled_module.run_scheduled_window(
        conn=object(),
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 30),
        opensearch_config=OpenSearchConfig(base_url="https://opensearch.example.test"),
    )

    # one call per resource (5 resources), each indexing its synthetic act
    assert len(indexed_act_ids) == 5


async def test_does_not_index_when_opensearch_not_configured(monkeypatch):
    index_calls = []

    async def _spy_index_single_act(conn, http_client, config, act_id):
        index_calls.append(act_id)

    monkeypatch.setattr(scheduled_module, "index_single_act", _spy_index_single_act)

    async def _fake_ingest_khmdhs_partition(
        *, client, raw_store, conn, resource, date_from, date_to, on_ingest_result, enrich_deduplicated
    ):
        assert enrich_deduplicated is True
        await on_ingest_result(conn, resource, _make_ingest_result())
        return _FakePartitionResult()

    monkeypatch.setattr(scheduled_module, "ingest_khmdhs_partition", _fake_ingest_khmdhs_partition)

    from datetime import date

    await scheduled_module.run_scheduled_window(
        conn=object(), date_from=date(2025, 1, 1), date_to=date(2025, 1, 30), opensearch_config=None
    )

    assert index_calls == []


async def test_an_indexing_failure_is_logged_and_does_not_break_ingestion(monkeypatch, caplog):
    async def _failing_index_single_act(conn, http_client, config, act_id):
        raise RuntimeError("opensearch is down")

    monkeypatch.setattr(scheduled_module, "index_single_act", _failing_index_single_act)

    async def _fake_ingest_khmdhs_partition(
        *, client, raw_store, conn, resource, date_from, date_to, on_ingest_result, enrich_deduplicated
    ):
        assert enrich_deduplicated is True
        await on_ingest_result(conn, resource, _make_ingest_result())
        return _FakePartitionResult()

    monkeypatch.setattr(scheduled_module, "ingest_khmdhs_partition", _fake_ingest_khmdhs_partition)

    from datetime import date

    result = await scheduled_module.run_scheduled_window(
        conn=object(),
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 30),
        opensearch_config=OpenSearchConfig(base_url="https://opensearch.example.test"),
    )
    # ingestion totals still reported normally despite every indexing call failing
    assert result["records_upserted"] == 5


async def test_daily_document_downloads_respect_their_own_budget(monkeypatch):
    processed = []

    async def _missing(*args, **kwargs):
        return False

    async def _process(*args, **kwargs):
        processed.append(kwargs["adam"])
        return object()

    async def _fake_ingest_khmdhs_partition(
        *, client, raw_store, conn, resource, date_from, date_to, on_ingest_result, enrich_deduplicated
    ):
        await on_ingest_result(conn, resource, _make_ingest_result())
        return _FakePartitionResult()

    monkeypatch.setattr(scheduled_module, "has_khmdhs_attachment", _missing)
    monkeypatch.setattr(scheduled_module, "process_khmdhs_attachment", _process)
    monkeypatch.setattr(scheduled_module, "ingest_khmdhs_partition", _fake_ingest_khmdhs_partition)

    from datetime import date

    result = await scheduled_module.run_scheduled_window(
        conn=object(),
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 1),
        process_documents=True,
        provider_lookup_budgets={"KHMDHS_DOCUMENT": 2},
    )

    assert len(processed) == 2
    assert result["enrichment_attempts"]["KHMDHS_DOCUMENT"] == 2
    assert result["enrichment_deferred"]["KHMDHS_DOCUMENT"] == 3
