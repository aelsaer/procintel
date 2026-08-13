"""OGC GetCapabilities parsing and persistent Ktimatologio health checks."""

from __future__ import annotations

import hashlib
import uuid
from defusedxml import ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
    data_quality_issues,
    external_datasets,
    source_records,
    spatial_service_capabilities,
)
from packages.source_clients.raw_store import RawStore


@dataclass(frozen=True)
class CapabilityCheckResult:
    status: str
    http_status: int | None
    layer_count: int
    formats: tuple[str, ...]
    error: str | None = None
    checked: bool = True


def _capability_source_record_insert(values: dict[str, Any]):
    return (
        pg_insert(source_records)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=(
                source_records.c.source_system,
                source_records.c.resource_type,
                source_records.c.content_sha256,
            )
        )
        .returning(source_records.c.id)
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root: ET.Element, *local_names: str) -> str | None:
    wanted = set(local_names)
    for element in root.iter():
        if _local_name(element.tag) in wanted and element.text and element.text.strip():
            return element.text.strip()
    return None


def parse_wms_capabilities(payload: bytes) -> dict[str, Any]:
    root = ET.fromstring(payload)
    root_name = _local_name(root.tag)
    if root_name not in {"WMS_Capabilities", "WMT_MS_Capabilities"}:
        raise ValueError(f"unexpected WMS capabilities root {root_name!r}")
    layers: list[dict[str, str | None]] = []
    formats: list[str] = []
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "Format" and element.text and element.text.strip():
            formats.append(element.text.strip())
        if local != "Layer":
            continue
        name = title = None
        for child in element:
            child_name = _local_name(child.tag)
            if child_name == "Name" and child.text:
                name = child.text.strip()
            elif child_name == "Title" and child.text:
                title = child.text.strip()
        if name:
            layers.append({"name": name, "title": title})
    return {
        "version": root.attrib.get("version"),
        "title": _first_text(root, "Title"),
        "provider_name": _first_text(root, "ProviderName", "ContactOrganization"),
        "access_constraints": _first_text(root, "AccessConstraints"),
        "fees": _first_text(root, "Fees"),
        "formats": list(dict.fromkeys(formats)),
        "layers": layers,
    }


def parse_wfs_capabilities(payload: bytes) -> dict[str, Any]:
    root = ET.fromstring(payload)
    root_name = _local_name(root.tag)
    if root_name != "WFS_Capabilities":
        raise ValueError(f"unexpected WFS capabilities root {root_name!r}")
    feature_types: list[dict[str, str | None]] = []
    formats: list[str] = []
    for element in root.iter():
        local = _local_name(element.tag)
        if local in {"Format", "OutputFormat"} and element.text and element.text.strip():
            formats.append(element.text.strip())
        if local != "FeatureType":
            if local == "Parameter" and element.attrib.get("name", "").casefold() == "outputformat":
                for child in element.iter():
                    if _local_name(child.tag) == "Value" and child.text and child.text.strip():
                        formats.append(child.text.strip())
            continue
        name = title = None
        for child in element:
            child_name = _local_name(child.tag)
            if child_name == "Name" and child.text:
                name = child.text.strip()
            elif child_name == "Title" and child.text:
                title = child.text.strip()
        if name:
            feature_types.append({"name": name, "title": title})
    return {
        "version": root.attrib.get("version"),
        "title": _first_text(root, "Title"),
        "provider_name": _first_text(root, "ProviderName", "ContactOrganization"),
        "access_constraints": _first_text(root, "AccessConstraints"),
        "fees": _first_text(root, "Fees"),
        "formats": list(dict.fromkeys(formats)),
        "layers": feature_types,
    }


def capability_health(parsed: dict[str, Any]) -> tuple[str, str | None]:
    if not parsed.get("layers"):
        return "DEGRADED", "capabilities advertise no queryable layers"
    return "AVAILABLE", None


def capability_quality_issue(status: str) -> tuple[str, str] | None:
    return {
        "BLOCKED_UPSTREAM": ("OGC_SERVICE_BLOCKED", "ERROR"),
        "INVALID_CAPABILITIES": ("OGC_CAPABILITIES_INVALID", "ERROR"),
        "DEGRADED": ("OGC_CAPABILITIES_DEGRADED", "WARNING"),
    }.get(status)


async def _sync_capability_quality_issue(
    conn: AsyncConnection,
    *,
    capability_id: uuid.UUID,
    service_url: str,
    service_type: str,
    status: str,
    error: str | None,
) -> None:
    issue = capability_quality_issue(status)
    issue_codes = (
        "OGC_SERVICE_BLOCKED",
        "OGC_CAPABILITIES_INVALID",
        "OGC_CAPABILITIES_DEGRADED",
    )
    if issue is None:
        await conn.execute(
            data_quality_issues.update()
            .where(
                data_quality_issues.c.object_type == "SPATIAL_SERVICE",
                data_quality_issues.c.object_id == capability_id,
                data_quality_issues.c.issue_code.in_(issue_codes),
                data_quality_issues.c.status.in_(("OPEN", "ACKNOWLEDGED")),
            )
            .values(status="RESOLVED", resolved_at=datetime.now(timezone.utc))
        )
        return

    issue_code, severity = issue
    await conn.execute(
        data_quality_issues.update()
        .where(
            data_quality_issues.c.object_type == "SPATIAL_SERVICE",
            data_quality_issues.c.object_id == capability_id,
            data_quality_issues.c.issue_code.in_(issue_codes),
            data_quality_issues.c.issue_code != issue_code,
            data_quality_issues.c.status.in_(("OPEN", "ACKNOWLEDGED")),
        )
        .values(status="RESOLVED", resolved_at=datetime.now(timezone.utc))
    )
    existing_id = (
        await conn.execute(
            sa.select(data_quality_issues.c.id).where(
                data_quality_issues.c.object_type == "SPATIAL_SERVICE",
                data_quality_issues.c.object_id == capability_id,
                data_quality_issues.c.issue_code == issue_code,
                data_quality_issues.c.status.in_(("OPEN", "ACKNOWLEDGED")),
            )
        )
    ).scalar()
    details = {
        "service_url": service_url,
        "service_type": service_type,
        "capability_status": status,
        "message": error,
    }
    if existing_id is None:
        await conn.execute(
            data_quality_issues.insert().values(
                id=uuid.uuid4(),
                object_type="SPATIAL_SERVICE",
                object_id=capability_id,
                issue_code=issue_code,
                severity=severity,
                details=details,
            )
        )
    else:
        await conn.execute(
            data_quality_issues.update()
            .where(data_quality_issues.c.id == existing_id)
            .values(severity=severity, details=details, status="OPEN")
        )


async def _recent_blocked_result(
    conn: AsyncConnection,
    *,
    service_url: str,
    service_type: str,
    blocked_retry_after: timedelta | None,
    now: datetime,
) -> CapabilityCheckResult | None:
    if blocked_retry_after is None:
        return None
    row = (
        await conn.execute(
            sa.select(
                spatial_service_capabilities.c.status,
                spatial_service_capabilities.c.http_status,
                spatial_service_capabilities.c.layers,
                spatial_service_capabilities.c.formats,
                spatial_service_capabilities.c.last_error,
                spatial_service_capabilities.c.checked_at,
            ).where(
                spatial_service_capabilities.c.service_url == service_url,
                spatial_service_capabilities.c.service_type == service_type,
            )
        )
    ).mappings().first()
    if (
        row is None
        or row["status"] != "BLOCKED_UPSTREAM"
        or row["checked_at"] is None
        or now - row["checked_at"] >= blocked_retry_after
    ):
        return None
    last_error = row["last_error"] or {}
    return CapabilityCheckResult(
        status=row["status"],
        http_status=row["http_status"],
        layer_count=len(row["layers"] or []),
        formats=tuple(row["formats"] or []),
        error=last_error.get("message"),
        checked=False,
    )


async def validate_ogc_service(
    conn: AsyncConnection,
    *,
    http_client: httpx.AsyncClient,
    raw_store: RawStore,
    service_url: str,
    service_type: str,
    catalog_source: str,
    catalog_dataset_id: str,
    title: str,
    publisher: str | None,
    license_code: str | None = None,
    config: dict[str, Any] | None = None,
    blocked_retry_after: timedelta | None = None,
) -> CapabilityCheckResult:
    service_type = service_type.upper()
    if service_type not in {"WMS", "WFS"}:
        raise ValueError(f"unsupported OGC service type {service_type!r}")
    checked_at = datetime.now(timezone.utc)
    cached = await _recent_blocked_result(
        conn,
        service_url=service_url,
        service_type=service_type,
        blocked_retry_after=blocked_retry_after,
        now=checked_at,
    )
    if cached is not None:
        return cached

    params = {
        "SERVICE": service_type,
        "REQUEST": "GetCapabilities",
        "VERSION": "1.3.0" if service_type == "WMS" else "2.0.0",
    }
    response: httpx.Response | None = None
    payload = b""
    error: str | None = None
    parsed: dict[str, Any] = {}
    try:
        response = await http_client.get(service_url, params=params)
        payload = response.content
        if response.is_success:
            parser = parse_wms_capabilities if service_type == "WMS" else parse_wfs_capabilities
            parsed = parser(payload)
            status, error = capability_health(parsed)
            required_layer = (config or {}).get("required_layer")
            advertised_layers = {
                item.get("name") for item in parsed.get("layers", [])
            }
            if (
                status == "AVAILABLE"
                and required_layer is not None
                and required_layer not in advertised_layers
            ):
                status = "DEGRADED"
                error = f"required layer {required_layer!r} is not advertised"
        else:
            status = "BLOCKED_UPSTREAM" if response.status_code in {404, 410} else "DEGRADED"
            error = f"HTTP {response.status_code}"
    except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
        status = (
            "INVALID_CAPABILITIES"
            if response is not None and response.is_success
            else "BLOCKED_UPSTREAM"
        )
        error = f"{type(exc).__name__}: {exc}"

    content_sha256 = hashlib.sha256(payload).hexdigest() if payload else None
    parsed_successfully = response is not None and response.is_success and bool(parsed)
    source_record_id = None
    if payload:
        raw_ref = await raw_store.put(
            source="inspire",
            resource=f"ogc_{service_type.lower()}_capabilities",
            partition_key=f"service={hashlib.sha256(service_url.encode()).hexdigest()[:16]}",
            payload=payload,
        )
        candidate_id = uuid.uuid4()
        source_record_id = (
            await conn.execute(
                _capability_source_record_insert(
                    {
                        "id": candidate_id,
                        "source_system": "INSPIRE",
                        "resource_type": f"{service_type}_CAPABILITIES",
                        "source_native_id": service_url,
                        "content_sha256": raw_ref.content_sha256,
                        "payload_uri": raw_ref.payload_uri,
                        "fetched_at": checked_at,
                        "http_status": response.status_code if response is not None else None,
                        "license_code": license_code or "UNCONFIRMED",
                        "parse_status": "PARSED" if parsed_successfully else "FAILED",
                        "parse_error": {"message": error} if error else None,
                    }
                )
            )
        ).scalar()
        if source_record_id is None:
            source_record_id = (
                await conn.execute(
                    sa.select(source_records.c.id).where(
                        source_records.c.source_system == "INSPIRE",
                        source_records.c.resource_type == f"{service_type}_CAPABILITIES",
                        source_records.c.content_sha256 == raw_ref.content_sha256,
                    )
                )
            ).scalar_one()

    resolved_license = parsed.get("access_constraints") or license_code or "UNCONFIRMED"
    dataset_config = {
        "service_type": service_type,
        "provenance_role": "REFERENCE",
        **(config or {}),
    }
    ingestion_status = (
        "ONBOARDED"
        if status == "AVAILABLE"
        else "DEGRADED"
        if status == "DEGRADED"
        else "BLOCKED"
    )
    await conn.execute(
        pg_insert(external_datasets)
        .values(
            id=uuid.uuid4(),
            catalog_source=catalog_source,
            catalog_dataset_id=catalog_dataset_id,
            title=title,
            publisher=publisher,
            license_code=resolved_license,
            resource_type=service_type,
            resource_url=service_url,
            last_seen_at=checked_at,
            ingestion_status=ingestion_status,
            adapter_name=f"ogc_{service_type.lower()}",
            config=dataset_config,
        )
        .on_conflict_do_update(
            index_elements=[
                external_datasets.c.catalog_source,
                external_datasets.c.catalog_dataset_id,
            ],
            set_={
                "title": title,
                "publisher": publisher,
                "license_code": resolved_license,
                "resource_type": service_type,
                "resource_url": service_url,
                "last_seen_at": checked_at,
                "ingestion_status": ingestion_status,
                "adapter_name": f"ogc_{service_type.lower()}",
                "config": dataset_config,
            },
        )
    )
    update_values = {
        "catalog_source": catalog_source,
        "service_type": service_type,
        "service_version": parsed.get("version"),
        "title": parsed.get("title") or title,
        "provider_name": parsed.get("provider_name") or publisher,
        "access_constraints": resolved_license,
        "fees": parsed.get("fees"),
        "formats": parsed.get("formats", []),
        "layers": parsed.get("layers", []),
        "status": status,
        "http_status": response.status_code if response is not None else None,
        "content_sha256": content_sha256,
        "source_record_id": source_record_id,
        "last_error": {"message": error} if error else None,
        "checked_at": checked_at,
    }
    if status == "AVAILABLE":
        update_values["last_available_at"] = checked_at
    capability_id = (
        await conn.execute(
            pg_insert(spatial_service_capabilities)
            .values(id=uuid.uuid4(), service_url=service_url, **update_values)
            .on_conflict_do_update(
                index_elements=[
                    spatial_service_capabilities.c.service_url,
                    spatial_service_capabilities.c.service_type,
                ],
                set_=update_values,
            )
            .returning(spatial_service_capabilities.c.id)
        )
    ).scalar_one()
    await _sync_capability_quality_issue(
        conn,
        capability_id=capability_id,
        service_url=service_url,
        service_type=service_type,
        status=status,
        error=error,
    )
    await conn.commit()
    return CapabilityCheckResult(
        status=status,
        http_status=response.status_code if response is not None else None,
        layer_count=len(parsed.get("layers", [])),
        formats=tuple(parsed.get("formats", [])),
        error=error,
    )


async def validate_wms_service(
    conn: AsyncConnection,
    *,
    http_client: httpx.AsyncClient,
    raw_store: RawStore,
    service_url: str,
    blocked_retry_after: timedelta | None = None,
) -> CapabilityCheckResult:
    return await validate_ogc_service(
        conn,
        http_client=http_client,
        raw_store=raw_store,
        service_url=service_url,
        service_type="WMS",
        catalog_source="KTIMATOLOGIO_INSPIRE",
        catalog_dataset_id="KTIMATOLOGIO_WMS",
        title="Γεωπύλη INSPIRE Ελληνικού Κτηματολογίου",
        publisher="Ελληνικό Κτηματολόγιο",
        config={"cadastral_parcels_in_scope": False, "authoritative": True},
        blocked_retry_after=blocked_retry_after,
    )
