"""TED ingestion pipeline — mirrors
services/ingestion/connectors/khmdhs/pipeline.py's shape, but TED notices
are ingested as their own standalone backfill (§21.1: "μαζική λήψη XML")
rather than being triggered by another connector — nothing on the ΚΗΜΔΗΣ
side names a TED notice up front (see resolve.py's module docstring).

fetch page -> save raw response -> normalize -> upsert -> repeat until
`is_last_page`. `on_notice_upserted` is an optional hook fired after each
notice whose content was actually new — `resolve.py` (process matching) and
VIES (foreign supplier validation) both plug in here via `cli.py`.

Per-record isolation, mirroring the fix in
`connectors/khmdhs/pipeline.py`: one malformed notice or one flaky
`on_notice_upserted` hook (process matching / VIES hitting a live API
hiccup) must not sacrifice every other already-valid notice in the same
page. See that module's docstring for why this doesn't use a savepoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from packages.source_clients.raw_store import RawStore

from .client import TedClient
from .db_writer import TedIngestResult, ingest_notice_record, load_existing_notice_context

OnNoticeUpserted = Callable[[AsyncConnection, TedIngestResult], Awaitable[None]]

_MAX_RECORDED_FAILURES = 50


def _describe_exception(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

_NOTICE_ID_FALLBACK_KEYS = (
    "notice-identifier",
    "publication-number",
    "noticeId",
    "id",
    "publicationNumber",
)


def _extract_notice_id(notice: dict) -> str:
    for key in _NOTICE_ID_FALLBACK_KEYS:
        value = notice.get(key)
        if value:
            if isinstance(value, list):
                return str(value[0]) if value else "UNKNOWN"
            if isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, list) and nested:
                        return str(nested[0])
                    if nested:
                        return str(nested)
            return str(value)
    return "UNKNOWN"


def _stable_json_bytes(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class TedPartitionIngestResult:
    pages_fetched: int
    notices_seen: int
    notices_ingested: int  # excludes dedup hits
    notices_failed: int = 0
    failed_notices: list[dict[str, Any]] = field(default_factory=list)


async def ingest_ted_partition(
    *,
    client: TedClient,
    raw_store: RawStore,
    conn: AsyncConnection,
    country: str,
    date_from: date,
    date_to: date,
    on_notice_upserted: OnNoticeUpserted | None = None,
    enrich_deduplicated: bool = False,
) -> TedPartitionIngestResult:
    page = 0
    iteration_next_token: str | None = None
    pages_fetched = 0
    notices_seen = 0
    notices_ingested = 0
    notices_failed = 0
    failed_notices: list[dict[str, Any]] = []

    while True:
        page_result = await client.search_notices(
            country=country,
            date_from=date_from,
            date_to=date_to,
            page=page,
            iteration_next_token=iteration_next_token,
        )
        pages_fetched += 1

        for notice in page_result.notices:
            notices_seen += 1
            notice_id = _extract_notice_id(notice)

            try:
                raw_ref = await raw_store.put(
                    source="ted",
                    resource="notice",
                    partition_key=f"notice={notice_id}",
                    payload=_stable_json_bytes(notice),
                )
                ingest_result = await ingest_notice_record(
                    conn,
                    ted_notice_id=notice_id,
                    raw_body=notice,
                    raw_format="JSON",
                    payload_uri=raw_ref.payload_uri,
                    content_sha256=raw_ref.content_sha256,
                    http_status=page_result.http_status,
                    fetched_at=datetime.now(timezone.utc),
                )
            except Exception as exc:  # noqa: BLE001 — one bad notice must not sacrifice the rest of the page
                notices_failed += 1
                if len(failed_notices) < _MAX_RECORDED_FAILURES:
                    failed_notices.append({"notice_id": notice_id, "stage": "ingest", "error": _describe_exception(exc)})
            else:
                if ingest_result.source_record_id is not None:
                    notices_ingested += 1
                if on_notice_upserted is not None:
                    try:
                        if ingest_result.source_record_id is None and enrich_deduplicated:
                            ingest_result = await load_existing_notice_context(
                                conn,
                                ted_notice_id=notice_id,
                                raw_body=notice,
                            )
                        if ingest_result.notice is not None:
                            await on_notice_upserted(conn, ingest_result)
                    except Exception as exc:  # noqa: BLE001 — the notice already ingested fine; don't lose it over a flaky hook
                        notices_failed += 1
                        if len(failed_notices) < _MAX_RECORDED_FAILURES:
                            failed_notices.append(
                                {"notice_id": notice_id, "stage": "on_notice_upserted", "error": _describe_exception(exc)}
                            )

        await conn.commit()

        if page_result.is_last_page:
            break
        iteration_next_token = page_result.iteration_next_token
        page += 1

    return TedPartitionIngestResult(
        pages_fetched=pages_fetched,
        notices_seen=notices_seen,
        notices_ingested=notices_ingested,
        notices_failed=notices_failed,
        failed_notices=failed_notices,
    )
