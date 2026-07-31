"""ΚΗΜΔΗΣ ingestion pipeline — description.txt §16.4 — shared across all
five resources (request/notice/auction/contract/payment).

fetch page -> save raw response -> normalize -> upsert staging/canonical ->
repeat until `is_last_page`. Each record gets its own raw object, keyed by
ΑΔΑΜ, mirroring the §13.1 example path
(`raw/khmdhs/<resource>/ingestion_date=.../adam=<ADAM>/<sha>.json`).

`on_ingest_result` is an optional hook fired after each record whose content
was actually new (`IngestResult.source_record_id is not None`), with the
full `IngestResult` (act id/type, insert-vs-changed-fields). Scheduled daily
runs may set `enrich_deduplicated=True` to rebuild trigger context for
unchanged records and retry provider work across the rolling overlap. adamChain
resolution (adamchain.py, Phase B) and alert evaluation
(services/alerts/evaluate.py, Phase E) both plug in here rather than this
module importing either directly — the CLI composes them into one callback
— so the pipeline itself stays usable without either's extra work when
that's not wanted (e.g. a heavy historical backfill).

Cursor/watermark persistence (`source_cursors`) is not wired up in this
slice — that belongs to services/ingestion/orchestration (Στάδιο 1). This
pipeline is safe to call repeatedly (every write below is idempotent), but
it does not yet track "resume from where I left off" across restarts.

Per-record isolation: one malformed record (an unexpected field shape a
live, messy government API turns up — e.g. the `procedureType` coded-object
bug this was built to survive) or one flaky enrichment-hook call (adamChain/
alerts/ΓΕΜΗ/... hitting a live API hiccup) must not sacrifice every other
already-valid record in the same page. Both the core `ingest_khmdhs_record`
call and the `on_ingest_result` hook are wrapped per-record — a failure is
recorded in `PartitionIngestResult.failed_records` and the loop moves on to
the next record, rather than the whole page/partition aborting.

Core canonical writes run inside one savepoint per record. This prevents a
constraint violation in one live record from leaving the outer page
transaction aborted and turning every later row into a false failure. The
enrichment callback runs after that savepoint because some composed hooks
commit their own work; callback failures are counted separately and do not
alter the core-ingestion result.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from packages.source_clients.raw_store import RawStore

from .client import KhmdhsClient, KhmdhsResource
from .db_writer import IngestResult, ingest_khmdhs_record, load_existing_act_context

OnIngestResult = Callable[[AsyncConnection, str, IngestResult], Awaitable[None]]  # (conn, resource, result) -> None

_MAX_RECORDED_FAILURES = 50  # a bounded sample is enough to diagnose; unbounded risks a huge print/log for a bad day


def _describe_exception(exc: Exception) -> str:
    # str(exc) is empty for several real-world exceptions (notably httpx
    # timeout errors) — always include the type name so a failure is
    # actually diagnosable instead of a blank message.
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


@dataclass(frozen=True)
class PartitionIngestResult:
    resource: str
    pages_fetched: int
    records_seen: int
    records_ingested: int  # excludes dedup hits (unchanged content re-ingested)
    records_unchanged: int = 0
    records_failed: int = 0  # core ingestion or enrichment-hook failures, per-record — see module docstring
    core_records_failed: int = 0
    enrichment_callbacks_failed: int = 0
    failed_records: list[dict[str, Any]] = field(default_factory=list)  # bounded sample: [{"adam", "stage", "error"}]
    reached_page_budget: bool = False
    reached_record_budget: bool = False


def _stable_json_bytes(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")


@asynccontextmanager
async def _record_savepoint(conn: AsyncConnection):
    """Keep one bad canonical write from aborting every later row on a page."""
    begin_nested = getattr(conn, "begin_nested", None)
    if begin_nested is None:  # lightweight unit-test connections
        yield
        return
    async with begin_nested():
        yield


async def ingest_khmdhs_partition(
    *,
    client: KhmdhsClient,
    raw_store: RawStore,
    conn: AsyncConnection,
    resource: KhmdhsResource,
    date_from: date,
    date_to: date,
    on_ingest_result: OnIngestResult | None = None,
    enrich_deduplicated: bool = False,
    max_pages: int | None = None,
    max_records: int | None = None,
) -> PartitionIngestResult:
    if max_pages is not None and max_pages <= 0:
        raise ValueError("max_pages must be positive when set")
    if max_records is not None and max_records <= 0:
        raise ValueError("max_records must be positive when set")

    page = 0
    pages_fetched = 0
    records_seen = 0
    records_ingested = 0
    records_unchanged = 0
    records_failed = 0
    core_records_failed = 0
    enrichment_callbacks_failed = 0
    failed_records: list[dict[str, Any]] = []
    reached_page_budget = False
    reached_record_budget = False

    while True:
        page_result = await client.fetch_resource_page(
            resource=resource, page=page, date_from=date_from, date_to=date_to
        )
        pages_fetched += 1

        for record in page_result.records:
            records_seen += 1
            adam = str(record.get("referenceNumber", "UNKNOWN"))

            try:
                raw_ref = await raw_store.put(
                    source="khmdhs",
                    resource=resource,
                    partition_key=f"adam={adam}",
                    payload=_stable_json_bytes(record),
                )
                async with _record_savepoint(conn):
                    ingest_result = await ingest_khmdhs_record(
                        conn,
                        resource=resource,
                        raw_record=record,
                        payload_uri=raw_ref.payload_uri,
                        content_sha256=raw_ref.content_sha256,
                        http_status=page_result.http_status,
                        fetched_at=datetime.now(timezone.utc),
                    )
            except Exception as exc:  # noqa: BLE001 — one bad record must not sacrifice the rest of the page, see module docstring
                records_failed += 1
                core_records_failed += 1
                if len(failed_records) < _MAX_RECORDED_FAILURES:
                    failed_records.append({"adam": adam, "stage": "ingest", "error": _describe_exception(exc)})
            else:
                if ingest_result.source_record_id is not None:
                    records_ingested += 1
                else:
                    records_unchanged += 1
                if on_ingest_result is not None:
                    try:
                        if ingest_result.source_record_id is None and enrich_deduplicated:
                            ingest_result = await load_existing_act_context(
                                conn,
                                resource=resource,
                                raw_record=record,
                            )
                        if ingest_result.act_upsert is not None:
                            await on_ingest_result(conn, resource, ingest_result)
                    except Exception as exc:  # noqa: BLE001 — the core record already ingested fine; don't lose it over a flaky enrichment hook
                        records_failed += 1
                        enrichment_callbacks_failed += 1
                        if len(failed_records) < _MAX_RECORDED_FAILURES:
                            failed_records.append(
                                {"adam": adam, "stage": "on_ingest_result", "error": _describe_exception(exc)}
                            )

            if max_records is not None and records_seen >= max_records:
                reached_record_budget = True
                break

        await conn.commit()

        if reached_record_budget:
            break
        if page_result.is_last_page:
            break
        if max_pages is not None and pages_fetched >= max_pages:
            reached_page_budget = True
            break
        page += 1

    return PartitionIngestResult(
        resource=resource,
        pages_fetched=pages_fetched,
        records_seen=records_seen,
        records_ingested=records_ingested,
        records_unchanged=records_unchanged,
        records_failed=records_failed,
        core_records_failed=core_records_failed,
        enrichment_callbacks_failed=enrichment_callbacks_failed,
        failed_records=failed_records,
        reached_page_budget=reached_page_budget,
        reached_record_budget=reached_record_budget,
    )
