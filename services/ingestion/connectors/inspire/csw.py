"""Greek INSPIRE CSW discovery and service-reference normalization."""

from __future__ import annotations

import hashlib
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import source_records, spatial_service_capabilities
from packages.source_clients.raw_store import RawStore

from .capabilities import validate_ogc_service

MAX_CSW_PAYLOAD_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class CswRecord:
    identifier: str
    title: str
    publisher: str | None
    modified: str | None
    license_code: str | None
    references: tuple[str, ...]
    formats: tuple[str, ...]
    subjects: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveredSpatialService:
    record_id: str
    title: str
    publisher: str | None
    modified: str | None
    license_code: str | None
    service_type: str
    service_url: str
    catalog_source: str


@dataclass(frozen=True)
class CswDiscoveryResult:
    records_seen: int
    services_discovered: int
    services_checked: int
    services_skipped: int
    available: int
    degraded: int
    blocked: int
    invalid: int
    error: str | None = None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _texts(element: ET.Element, *names: str) -> list[str]:
    wanted = set(names)
    values: list[str] = []
    for child in element.iter():
        if _local_name(child.tag) not in wanted:
            continue
        value = " ".join(part.strip() for part in child.itertext() if part.strip())
        if value and value not in values:
            values.append(value)
    return values


def _first(values: Iterable[str]) -> str | None:
    return next(iter(values), None)


def parse_csw_records(payload: bytes) -> list[CswRecord]:
    if len(payload) > MAX_CSW_PAYLOAD_BYTES:
        raise ValueError("CSW response exceeds the 10 MiB safety limit")
    root = ET.fromstring(payload)
    root_name = _local_name(root.tag)
    if root_name not in {"GetRecordsResponse", "Record", "SummaryRecord"}:
        raise ValueError(f"unexpected CSW response root {root_name!r}")

    elements = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"Record", "SummaryRecord"}
    ]
    if root_name in {"Record", "SummaryRecord"}:
        elements = [root]

    records: list[CswRecord] = []
    for index, element in enumerate(elements):
        identifiers = _texts(element, "identifier", "fileIdentifier")
        titles = _texts(element, "title")
        references = tuple(_texts(element, "references", "URI", "URL", "linkage"))
        if not references:
            references = tuple(
                value
                for value in _texts(element, "OnlineResource")
                if value.startswith(("http://", "https://"))
            )
        identifier = _first(identifiers) or f"anonymous-record-{index}"
        records.append(
            CswRecord(
                identifier=identifier,
                title=_first(titles) or identifier,
                publisher=_first(
                    _texts(
                        element,
                        "publisher",
                        "creator",
                        "organisationName",
                        "organizationName",
                    )
                ),
                modified=_first(_texts(element, "modified", "dateStamp")),
                license_code=_first(
                    _texts(
                        element,
                        "license",
                        "rights",
                        "accessConstraints",
                        "useLimitation",
                    )
                ),
                references=references,
                formats=tuple(_texts(element, "format")),
                subjects=tuple(_texts(element, "subject", "keyword")),
            )
        )
    return records


def _service_type(url: str, record: CswRecord) -> str | None:
    parsed = urlparse(url)
    query = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
    service = _first(query.get("service", []))
    if service and service.upper() in {"WMS", "WFS"}:
        return service.upper()

    url_evidence = url.upper()
    for candidate in ("WFS", "WMS"):
        if re.search(rf"(?:^|[^A-Z]){candidate}(?:$|[^A-Z])", url_evidence):
            return candidate
    service_like_path = parsed.path.casefold().endswith(("/ows", "/service"))
    if service_like_path:
        record_evidence = " ".join((record.title, *record.formats, *record.subjects)).upper()
        for candidate in ("WFS", "WMS"):
            if re.search(rf"(?:^|[^A-Z]){candidate}(?:$|[^A-Z])", record_evidence):
                return candidate
    return None


def _canonical_service_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("not an HTTP service URL")
    operation_parameters = {
        "bbox",
        "count",
        "crs",
        "format",
        "height",
        "layers",
        "maxfeatures",
        "outputformat",
        "request",
        "service",
        "srs",
        "startindex",
        "styles",
        "typename",
        "typenames",
        "version",
        "width",
    }
    retained_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in operation_parameters
    ]
    return parsed._replace(query=urlencode(retained_query), fragment="").geturl()


def _catalog_source(record: CswRecord, url: str) -> str:
    evidence = " ".join((record.title, record.publisher or "", url)).casefold()
    if any(token in evidence for token in ("ktimanet", "ktimatologio", "κτηματολογ")):
        return "KTIMATOLOGIO_INSPIRE"
    return "GREEK_INSPIRE_CSW"


def discover_spatial_services(records: Iterable[CswRecord]) -> list[DiscoveredSpatialService]:
    discovered: list[DiscoveredSpatialService] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        for reference in record.references:
            try:
                service_type = _service_type(reference, record)
                service_url = _canonical_service_url(reference)
            except ValueError:
                continue
            if service_type is None:
                continue
            key = (service_type, service_url.casefold())
            if key in seen:
                continue
            seen.add(key)
            discovered.append(
                DiscoveredSpatialService(
                    record_id=record.identifier,
                    title=record.title,
                    publisher=record.publisher,
                    modified=record.modified,
                    license_code=record.license_code,
                    service_type=service_type,
                    service_url=service_url,
                    catalog_source=_catalog_source(record, service_url),
                )
            )
    return discovered


def select_services_for_check(
    services: Iterable[DiscoveredSpatialService],
    *,
    last_checked: dict[tuple[str, str], datetime],
    limit: int,
) -> list[DiscoveredSpatialService]:
    ordered = sorted(
        services,
        key=lambda service: (
            last_checked.get((service.service_url, service.service_type)) is not None,
            last_checked.get((service.service_url, service.service_type))
            or datetime.min.replace(tzinfo=timezone.utc),
            service.service_url,
            service.service_type,
        ),
    )
    return ordered[:limit]


def _next_record(payload: bytes) -> int:
    root = ET.fromstring(payload)
    for element in root.iter():
        if _local_name(element.tag) != "SearchResults":
            continue
        raw = element.attrib.get("nextRecord", "0")
        return int(raw) if raw.isdigit() else 0
    return 0


async def _fetch_csw_pages(
    http_client: httpx.AsyncClient,
    *,
    csw_url: str,
    max_records: int,
) -> tuple[list[CswRecord], list[bytes]]:
    records: list[CswRecord] = []
    payloads: list[bytes] = []
    start_position = 1
    while len(records) < max_records:
        page_size = min(100, max_records - len(records))
        response = await http_client.get(
            csw_url,
            params={
                "SERVICE": "CSW",
                "VERSION": "2.0.2",
                "REQUEST": "GetRecords",
                "RESULTTYPE": "results",
                "TYPENAMES": "csw:Record",
                "ELEMENTSETNAME": "full",
                "OUTPUTFORMAT": "application/xml",
                "MAXRECORDS": page_size,
                "STARTPOSITION": start_position,
                "CONSTRAINTLANGUAGE": "CQL_TEXT",
                "CONSTRAINT_LANGUAGE_VERSION": "1.1.0",
                "CONSTRAINT": "AnyText LIKE '%INSPIRE%'",
            },
        )
        response.raise_for_status()
        payload = response.content
        page_records = parse_csw_records(payload)
        payloads.append(payload)
        records.extend(page_records)
        next_record = _next_record(payload)
        if not page_records or next_record <= start_position:
            break
        start_position = next_record
    return records[:max_records], payloads


async def _record_csw_payload(
    conn: AsyncConnection,
    *,
    raw_store: RawStore,
    csw_url: str,
    payload: bytes,
    page_number: int,
) -> None:
    raw_ref = await raw_store.put(
        source="inspire",
        resource="greek_csw_catalog",
        partition_key=f"page={page_number}",
        payload=payload,
    )
    exists = (
        await conn.execute(
            sa.select(source_records.c.id).where(
                source_records.c.source_system == "INSPIRE",
                source_records.c.resource_type == "CSW_CATALOG",
                source_records.c.source_native_id == csw_url,
                source_records.c.content_sha256 == raw_ref.content_sha256,
            )
        )
    ).scalar()
    if exists is not None:
        return
    await conn.execute(
        source_records.insert().values(
            id=uuid.uuid4(),
            source_system="INSPIRE",
            resource_type="CSW_CATALOG",
            source_native_id=csw_url,
            content_sha256=raw_ref.content_sha256,
            payload_uri=raw_ref.payload_uri,
            fetched_at=datetime.now(timezone.utc),
            http_status=200,
            license_code="METADATA_ONLY",
            parse_status="PARSED",
        )
    )


async def discover_and_validate_csw_services(
    conn: AsyncConnection,
    *,
    http_client: httpx.AsyncClient,
    raw_store: RawStore,
    csw_url: str,
    max_records: int = 200,
    max_service_checks: int = 40,
    blocked_retry_after: timedelta = timedelta(days=30),
) -> CswDiscoveryResult:
    records, payloads = await _fetch_csw_pages(
        http_client,
        csw_url=csw_url,
        max_records=max_records,
    )
    for page_number, payload in enumerate(payloads, start=1):
        await _record_csw_payload(
            conn,
            raw_store=raw_store,
            csw_url=csw_url,
            payload=payload,
            page_number=page_number,
        )
    await conn.commit()

    services = discover_spatial_services(records)
    service_keys = [(service.service_url, service.service_type) for service in services]
    capability_rows = (
        (
            await conn.execute(
                sa.select(
                    spatial_service_capabilities.c.service_url,
                    spatial_service_capabilities.c.service_type,
                    spatial_service_capabilities.c.checked_at,
                ).where(
                    sa.tuple_(
                        spatial_service_capabilities.c.service_url,
                        spatial_service_capabilities.c.service_type,
                    ).in_(service_keys)
                )
            )
        ).mappings().all()
        if service_keys
        else []
    )
    last_checked = {
        (row["service_url"], row["service_type"]): row["checked_at"]
        for row in capability_rows
    }
    selected_services = select_services_for_check(
        services,
        last_checked=last_checked,
        limit=max_service_checks,
    )
    checked = 0
    skipped = len(services) - len(selected_services)
    available = degraded = blocked = invalid = 0
    for service in selected_services:
        service_hash = hashlib.sha256(service.service_url.encode()).hexdigest()[:12]
        result = await validate_ogc_service(
            conn,
            http_client=http_client,
            raw_store=raw_store,
            service_url=service.service_url,
            service_type=service.service_type,
            catalog_source=service.catalog_source,
            catalog_dataset_id=f"{service.record_id}:{service.service_type}:{service_hash}",
            title=service.title,
            publisher=service.publisher,
            license_code=service.license_code,
            config={
                "discovered_via": csw_url,
                "catalog_record_id": service.record_id,
                "catalog_modified": service.modified,
                "coverage": "CATALOG_DECLARED",
                "authoritative": service.catalog_source == "KTIMATOLOGIO_INSPIRE",
                "cadastral_parcels_in_scope": False,
            },
            blocked_retry_after=blocked_retry_after,
        )
        if result.checked:
            checked += 1
        else:
            skipped += 1
        if result.status == "AVAILABLE":
            available += 1
        elif result.status == "DEGRADED":
            degraded += 1
        elif result.status == "BLOCKED_UPSTREAM":
            blocked += 1
        else:
            invalid += 1
    return CswDiscoveryResult(
        records_seen=len(records),
        services_discovered=len(services),
        services_checked=checked,
        services_skipped=skipped,
        available=available,
        degraded=degraded,
        blocked=blocked,
        invalid=invalid,
    )
