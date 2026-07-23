"""On-demand exact-identifier fetch requests.

Search remains a read-mostly API: if an exact ΑΔΑΜ/ΑΔΑ is not in the local
database yet, the API records a fetch request and returns immediately. This
module owns the bounded provider work behind that request, reusing the same
rate-limited clients as scheduled ingestion.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Mapping

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from packages.domain.tables import act_identifiers, fetch_requests, procurement_acts, process_members
from packages.source_clients.raw_store import LocalFilesystemRawStore
from services.ingestion.connectors.diavgeia.client import DiavgeiaClient
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.diavgeia.db_writer import ingest_decision_record
from services.ingestion.connectors.diavgeia.normalize import normalize_ada
from services.ingestion.connectors.diavgeia.resolve import resolve_decision_for_ada, resolve_decision_via_search
from services.ingestion.connectors.khmdhs.adamchain import _sync_process_summary, get_act_id_by_adam, resolve_adam_chain_for_act
from services.ingestion.connectors.khmdhs.client import KhmdhsClient, KhmdhsResource
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.db_writer import IngestResult, ingest_khmdhs_record
from services.ingestion.connectors.khmdhs.normalize import normalize_adam

IdentifierScheme = Literal["ADAM", "ADA"]
SourceSystem = Literal["KHMDHS", "DIAVGEIA"]
FetchStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "NOT_FOUND", "WAITING_FOR_CONFIG", "FAILED"]

ACTIVE_FETCH_STATUSES = {"QUEUED", "RUNNING"}
RETRYABLE_FETCH_STATUSES = {"FAILED", "WAITING_FOR_CONFIG"}

_ADAM_RE = re.compile(r"^\d{2}(REQ|PROC|AWRD|SYMV|PAY)[A-Z0-9]{6,}$")
_ADA_RE = re.compile(r"^[0-9A-ZΑ-Ω]{6,12}-[0-9A-ZΑ-Ω]{3,5}$")

_ADAM_RESOURCE_BY_TOKEN: dict[str, KhmdhsResource] = {
    "REQ": "request",
    "PROC": "notice",
    "AWRD": "auction",
    "SYMV": "contract",
    "PAY": "payment",
}

PROVIDER_RATE_POLICIES: dict[SourceSystem, dict[str, Any]] = {
    "KHMDHS": {
        "rate_limit_per_minute": 210,
        "official_ceiling_per_minute": 350,
        "window_days": 30,
        "notes": "Uses the documented ΚΗΜΔΗΣ date-window pattern and stays inside the 180-240/min target band.",
    },
    "DIAVGEIA": {
        "rate_limit_per_minute": 120,
        "official_ceiling_per_minute": None,
        "window_days": None,
        "notes": "Direct ΑΔΑ fetch first; general search is not required for this on-demand path.",
    },
}


@dataclass(frozen=True)
class IdentifierTarget:
    raw: str
    normalized: str
    scheme: IdentifierScheme
    source_system: SourceSystem


@dataclass(frozen=True)
class FetchOutcome:
    status: FetchStatus
    message: str
    result_act_id: uuid.UUID | None = None
    result_process_id: uuid.UUID | None = None
    metadata: dict[str, Any] | None = None


def classify_identifier(identifier: str) -> IdentifierTarget | None:
    raw = identifier.strip()
    normalized = re.sub(r"\s+", "", raw).upper()
    if not normalized:
        return None
    if _ADAM_RE.fullmatch(normalized):
        return IdentifierTarget(raw=raw, normalized=normalize_adam(normalized), scheme="ADAM", source_system="KHMDHS")
    if _ADA_RE.fullmatch(normalized):
        return IdentifierTarget(raw=raw, normalized=normalize_ada(normalized), scheme="ADA", source_system="DIAVGEIA")
    return None


def provider_is_configured(source_system: SourceSystem) -> bool:
    if source_system == "KHMDHS":
        return True
    if source_system == "DIAVGEIA":
        return True
    return False


def _provider_config_message(source_system: SourceSystem) -> str:
    if source_system == "KHMDHS":
        return "KHMDHS_API_BASE_URL is not set; the fetch request is recorded and will run after the provider URL is configured."
    return "DIAVGEIA_API_BASE_URL is not set; the fetch request is recorded and will run after the provider URL is configured."


def _infer_khmdhs_resource(adam_normalized: str) -> KhmdhsResource:
    for token, resource in _ADAM_RESOURCE_BY_TOKEN.items():
        if token in adam_normalized:
            return resource
    raise ValueError(f"cannot infer ΚΗΜΔΗΣ resource from ΑΔΑΜ {adam_normalized!r}")


def _infer_adam_year(adam_normalized: str) -> int:
    prefix = int(adam_normalized[:2])
    return 2000 + prefix


def _raw_key_value(value: Any, *, prefer: str = "key") -> Any:
    if not isinstance(value, dict):
        return value
    first_key = prefer
    second_key = "value" if prefer == "key" else "key"
    direct_value = value.get(first_key) or value.get(second_key)
    if direct_value:
        return direct_value
    if len(value) == 1:
        nested = next(iter(value.values()))
        if isinstance(nested, dict):
            return _raw_key_value(nested, prefer=prefer)
    return None


def _raw_organization_name(raw_record: dict[str, Any]) -> str | None:
    value = raw_record.get("organizationName") or _raw_key_value(raw_record.get("organization"), prefer="value")
    return str(value) if value else None


def _windows_for_year(year: int, *, max_days: int = 30) -> list[tuple[date, date]]:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=max_days - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def _not_found_scan_complete(metadata: Mapping[str, Any]) -> bool:
    planned = metadata.get("windows_planned")
    scanned = metadata.get("windows_scanned")
    if not isinstance(planned, int) or not isinstance(scanned, int):
        return False
    return scanned >= planned


async def _find_existing_act(
    conn: AsyncConnection, *, scheme: IdentifierScheme, identifier_normalized: str
) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    row = (
        await conn.execute(
            select(act_identifiers.c.act_id, procurement_acts.c.process_id)
            .select_from(act_identifiers.join(procurement_acts, procurement_acts.c.id == act_identifiers.c.act_id))
            .where(
                act_identifiers.c.scheme == scheme,
                act_identifiers.c.value_normalized == identifier_normalized,
            )
        )
    ).first()
    if row is None:
        return None
    return row.act_id, row.process_id


async def ensure_fetch_request(conn: AsyncConnection, identifier: str) -> uuid.UUID | None:
    """Create or refresh a fetch request for exact identifiers.

    Returns None for broad text queries. The caller decides whether to start
    a background worker for returned requests whose status is QUEUED.
    """
    target = classify_identifier(identifier)
    if target is None:
        return None

    now = datetime.now(timezone.utc)
    metadata = {"provider_rate_policy": PROVIDER_RATE_POLICIES[target.source_system]}

    existing_act = await _find_existing_act(
        conn, scheme=target.scheme, identifier_normalized=target.normalized
    )
    if existing_act is not None:
        act_id, process_id = existing_act
        row = (
            await conn.execute(
                select(fetch_requests.c.id).where(
                    fetch_requests.c.identifier_scheme == target.scheme,
                    fetch_requests.c.identifier_normalized == target.normalized,
                )
            )
        ).first()
        if row is None:
            request_id = uuid.uuid4()
            await conn.execute(
                fetch_requests.insert().values(
                    id=request_id,
                    identifier_raw=target.raw,
                    identifier_normalized=target.normalized,
                    identifier_scheme=target.scheme,
                    source_system=target.source_system,
                    status="SUCCEEDED",
                    message="Identifier is already available locally.",
                    result_act_id=act_id,
                    result_process_id=process_id,
                    finished_at=now,
                    request_metadata=metadata,
                    updated_at=now,
                )
            )
            await conn.commit()
            return request_id
        await conn.execute(
            fetch_requests.update()
            .where(fetch_requests.c.id == row.id)
            .values(
                status="SUCCEEDED",
                message="Identifier is already available locally.",
                result_act_id=act_id,
                result_process_id=process_id,
                finished_at=now,
                request_metadata=metadata,
                updated_at=now,
            )
        )
        await conn.commit()
        return row.id

    status: FetchStatus = "QUEUED" if provider_is_configured(target.source_system) else "WAITING_FOR_CONFIG"
    message = "Fetch queued; the UI can keep polling without waiting on the provider."
    if status == "WAITING_FOR_CONFIG":
        message = _provider_config_message(target.source_system)

    existing_request = (
        await conn.execute(
            select(fetch_requests).where(
                fetch_requests.c.identifier_scheme == target.scheme,
                fetch_requests.c.identifier_normalized == target.normalized,
            )
        )
    ).first()
    if existing_request is None:
        request_id = uuid.uuid4()
        await conn.execute(
            fetch_requests.insert().values(
                id=request_id,
                identifier_raw=target.raw,
                identifier_normalized=target.normalized,
                identifier_scheme=target.scheme,
                source_system=target.source_system,
                status=status,
                message=message,
                request_metadata=metadata,
                updated_at=now,
            )
        )
        await conn.commit()
        return request_id

    request_id = existing_request.id
    current_status = existing_request.status
    if current_status in ACTIVE_FETCH_STATUSES:
        return request_id
    if current_status == "NOT_FOUND" and _not_found_scan_complete(existing_request.request_metadata or {}):
        return request_id
    if current_status in RETRYABLE_FETCH_STATUSES or current_status in {"SUCCEEDED", "NOT_FOUND"}:
        await conn.execute(
            fetch_requests.update()
            .where(fetch_requests.c.id == request_id)
            .values(
                status=status,
                message=message,
                result_act_id=None,
                result_process_id=None,
                started_at=None,
                finished_at=None,
                last_attempt_at=None,
                next_retry_at=None,
                request_metadata=metadata,
                updated_at=now,
            )
        )
        await conn.commit()
    return request_id


async def mark_fetch_request_running(conn: AsyncConnection, request_id: uuid.UUID) -> bool:
    row = (await conn.execute(select(fetch_requests).where(fetch_requests.c.id == request_id))).first()
    if row is None or row.status != "QUEUED":
        return False
    now = datetime.now(timezone.utc)
    await conn.execute(
        fetch_requests.update()
        .where(fetch_requests.c.id == request_id)
        .values(
            status="RUNNING",
            message="Provider request is running in the background.",
            started_at=row.started_at or now,
            last_attempt_at=now,
            attempt_count=(row.attempt_count or 0) + 1,
            updated_at=now,
        )
    )
    await conn.commit()
    return True


async def finish_fetch_request(conn: AsyncConnection, request_id: uuid.UUID, outcome: FetchOutcome) -> None:
    now = datetime.now(timezone.utc)
    await conn.execute(
        fetch_requests.update()
        .where(fetch_requests.c.id == request_id)
        .values(
            status=outcome.status,
            message=outcome.message,
            result_act_id=outcome.result_act_id,
            result_process_id=outcome.result_process_id,
            finished_at=now if outcome.status not in ACTIVE_FETCH_STATUSES else None,
            request_metadata=outcome.metadata or {},
            updated_at=now,
        )
    )
    await conn.commit()


async def _run_optional_diavgeia_links(
    conn: AsyncConnection,
    *,
    raw_store: LocalFilesystemRawStore,
    result: IngestResult,
    raw_record: dict[str, Any],
) -> None:
    if result.act_upsert is None:
        return
    client = DiavgeiaClient(DiavgeiaConnectorConfig.from_env())
    try:
        linked_decisions = 0
        for ada in result.act_upsert.related_ada:
            decision_act_id = await resolve_decision_for_ada(
                conn,
                client=client,
                raw_store=raw_store,
                ada=ada,
                origin_act_id=result.act_upsert.act_id,
            )
            if decision_act_id is not None:
                linked_decisions += 1
        if linked_decisions:
            return

        title = raw_record.get("title")
        organization_name = _raw_organization_name(raw_record)
        if not title or not organization_name:
            return
        await resolve_decision_via_search(
            conn,
            client=client,
            raw_store=raw_store,
            origin_act_id=result.act_upsert.act_id,
            organization_query=organization_name,
            title_query=str(title),
            protocol_number=str(raw_record.get("protocolNumber")) if raw_record.get("protocolNumber") else None,
        )
    finally:
        await client.aclose()


async def _fetch_chain_member_records(
    conn: AsyncConnection,
    *,
    process_id: uuid.UUID | None,
    seed_adam_normalized: str,
    raw_root: str,
) -> int:
    if process_id is None:
        return 0

    rows = (
        await conn.execute(
            select(act_identifiers.c.value_normalized)
            .select_from(
                act_identifiers.join(procurement_acts, procurement_acts.c.id == act_identifiers.c.act_id).join(
                    process_members, process_members.c.act_id == procurement_acts.c.id
                )
            )
            .where(
                process_members.c.process_id == process_id,
                act_identifiers.c.scheme == "ADAM",
                act_identifiers.c.value_normalized != seed_adam_normalized,
                procurement_acts.c.title.is_(None),
            )
            .limit(12)
        )
    ).all()

    fetched = 0
    for row in rows:
        target = classify_identifier(row.value_normalized)
        if target is None or target.scheme != "ADAM":
            continue
        outcome = await _fetch_khmdhs_adam(
            conn,
            target=target,
            raw_root=raw_root,
            resolve_chain=False,
            fetch_chain_members=False,
        )
        if outcome.status == "SUCCEEDED":
            fetched += 1
    if fetched:
        await _sync_process_summary(conn, process_id)
        await conn.commit()
    return fetched


async def _fetch_khmdhs_adam(
    conn: AsyncConnection,
    *,
    target: IdentifierTarget,
    raw_root: str,
    resolve_chain: bool = True,
    fetch_chain_members: bool = True,
) -> FetchOutcome:
    resource = _infer_khmdhs_resource(target.normalized)
    year = _infer_adam_year(target.normalized)
    windows = _windows_for_year(year)
    config = KhmdhsConnectorConfig.from_env()
    client = KhmdhsClient(config)
    raw_store = LocalFilesystemRawStore(raw_root)
    pages_fetched = 0
    records_seen = 0
    records_upserted = 0
    empty_windows = 0
    chain_members_fetched = 0
    metadata: dict[str, Any] = {
        "provider_rate_policy": PROVIDER_RATE_POLICIES["KHMDHS"],
        "resource": resource,
        "year": year,
        "windows_planned": len(windows),
    }

    async def _after_ingest(result: IngestResult, raw_record: dict[str, Any]) -> uuid.UUID | None:
        nonlocal chain_members_fetched, records_upserted
        if result.source_record_id is not None:
            records_upserted += 1
        process_id = None
        if resolve_chain:
            process_id = await resolve_adam_chain_for_act(
                conn,
                client=client,
                raw_store=raw_store,
                seed_adam_normalized=result.adam_normalized,
            )
        await _run_optional_diavgeia_links(conn, raw_store=raw_store, result=result, raw_record=raw_record)
        if resolve_chain and fetch_chain_members:
            chain_members_fetched += await _fetch_chain_member_records(
                conn,
                process_id=process_id,
                seed_adam_normalized=result.adam_normalized,
                raw_root=raw_root,
            )
        return process_id

    try:
        for window_index, (date_from, date_to) in enumerate(windows, start=1):
            page = 0
            while True:
                try:
                    page_result = await client.fetch_resource_page(
                        resource=resource,
                        page=page,
                        date_from=date_from,
                        date_to=date_to,
                        reference_number=target.normalized,
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        empty_windows += 1
                        break
                    raise
                pages_fetched += 1
                for record in page_result.records:
                    records_seen += 1
                    record_adam = normalize_adam(str(record.get("referenceNumber", "")))
                    raw_ref = await raw_store.put(
                        source="khmdhs",
                        resource=resource,
                        partition_key=f"adam={record_adam or 'UNKNOWN'}",
                        payload=json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8"),
                    )
                    ingest_result = await ingest_khmdhs_record(
                        conn,
                        resource=resource,
                        raw_record=record,
                        payload_uri=raw_ref.payload_uri,
                        content_sha256=raw_ref.content_sha256,
                        http_status=page_result.http_status,
                        fetched_at=datetime.now(timezone.utc),
                    )
                    process_id = None
                    if ingest_result.act_upsert is not None:
                        process_id = await _after_ingest(ingest_result, record)
                    await conn.commit()

                    if record_adam == target.normalized:
                        act_id = ingest_result.act_upsert.act_id if ingest_result.act_upsert else await get_act_id_by_adam(conn, target.normalized)
                        if act_id is None:
                            return FetchOutcome(
                                status="FAILED",
                                message="The source returned the target ΑΔΑΜ but it could not be written locally.",
                                metadata=metadata,
                            )
                        act_row = (
                            await conn.execute(
                                select(procurement_acts.c.process_id).where(procurement_acts.c.id == act_id)
                            )
                        ).first()
                        metadata.update(
                            {
                                "windows_scanned": window_index,
                                "pages_fetched": pages_fetched,
                                "records_seen": records_seen,
                                "records_upserted": records_upserted,
                                "empty_windows": empty_windows,
                                "chain_members_fetched": chain_members_fetched,
                            }
                        )
                        return FetchOutcome(
                            status="SUCCEEDED",
                            message="Identifier fetched from ΚΗΜΔΗΣ and stored locally.",
                            result_act_id=act_id,
                            result_process_id=act_row.process_id if act_row is not None else process_id,
                            metadata=metadata,
                        )

                await conn.commit()
                if page_result.is_last_page:
                    break
                page += 1

        metadata.update(
            {
                "windows_scanned": len(windows),
                "pages_fetched": pages_fetched,
                "records_seen": records_seen,
                "records_upserted": records_upserted,
                "empty_windows": empty_windows,
                "chain_members_fetched": chain_members_fetched,
            }
        )
        return FetchOutcome(
            status="NOT_FOUND",
            message="ΚΗΜΔΗΣ responded for the inferred year/resource windows, but this ΑΔΑΜ was not found.",
            metadata=metadata,
        )
    finally:
        await client.aclose()


async def _fetch_diavgeia_ada(conn: AsyncConnection, *, target: IdentifierTarget, raw_root: str) -> FetchOutcome:
    from services.ingestion.connectors.diavgeia.client import DecisionNotFoundError

    config = DiavgeiaConnectorConfig.from_env()
    client = DiavgeiaClient(config)
    raw_store = LocalFilesystemRawStore(raw_root)
    metadata = {"provider_rate_policy": PROVIDER_RATE_POLICIES["DIAVGEIA"]}
    try:
        try:
            response = await client.fetch_decision_by_ada(target.normalized)
        except DecisionNotFoundError:
            return FetchOutcome(
                status="NOT_FOUND",
                message="Διαύγεια answered, but no decision was found for this ΑΔΑ.",
                metadata=metadata,
            )

        raw_ref = await raw_store.put(
            source="diavgeia",
            resource="decision",
            partition_key=f"ada={target.normalized}",
            payload=response.raw_body,
        )
        result = await ingest_decision_record(
            conn,
            ada=target.normalized,
            raw_body=response.body,
            payload_uri=raw_ref.payload_uri,
            content_sha256=raw_ref.content_sha256,
            http_status=response.http_status,
            fetched_at=datetime.now(timezone.utc),
        )
        await conn.commit()
        if result.act_id is None:
            return FetchOutcome(
                status="FAILED",
                message="Διαύγεια returned the decision, but it could not be written locally.",
                metadata=metadata,
            )
        act_row = (
            await conn.execute(select(procurement_acts.c.process_id).where(procurement_acts.c.id == result.act_id))
        ).first()
        return FetchOutcome(
            status="SUCCEEDED",
            message="Identifier fetched from Διαύγεια and stored locally.",
            result_act_id=result.act_id,
            result_process_id=act_row.process_id if act_row is not None else None,
            metadata=metadata,
        )
    finally:
        await client.aclose()


async def process_fetch_request(engine: AsyncEngine, request_id: uuid.UUID, *, raw_root: str = "./raw") -> None:
    async with engine.connect() as conn:
        if not await mark_fetch_request_running(conn, request_id):
            return
        row = (await conn.execute(select(fetch_requests).where(fetch_requests.c.id == request_id))).first()
        if row is None:
            return
        target = classify_identifier(row.identifier_normalized)
        if target is None:
            await finish_fetch_request(
                conn,
                request_id,
                FetchOutcome(status="FAILED", message="This fetch request does not contain a valid exact identifier."),
            )
            return

        try:
            if target.scheme == "ADAM":
                outcome = await _fetch_khmdhs_adam(conn, target=target, raw_root=raw_root)
            else:
                outcome = await _fetch_diavgeia_ada(conn, target=target, raw_root=raw_root)
        except RuntimeError as exc:
            outcome = FetchOutcome(
                status="WAITING_FOR_CONFIG",
                message=str(exc),
                metadata={"provider_rate_policy": PROVIDER_RATE_POLICIES[target.source_system]},
            )
        except Exception as exc:  # noqa: BLE001 - persisted failure is more useful than a lost background exception
            outcome = FetchOutcome(
                status="FAILED",
                message=str(exc),
                metadata={"provider_rate_policy": PROVIDER_RATE_POLICIES[target.source_system]},
            )
        await finish_fetch_request(conn, request_id, outcome)
