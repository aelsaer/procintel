"""OGC GetCapabilities parsing and persistent Ktimatologio health checks."""

from __future__ import annotations

import hashlib
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import (
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


async def validate_wms_service(
    conn: AsyncConnection,
    *,
    http_client: httpx.AsyncClient,
    raw_store: RawStore,
    service_url: str,
) -> CapabilityCheckResult:
    checked_at = datetime.now(timezone.utc)
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetCapabilities",
        "VERSION": "1.3.0",
    }
    response: httpx.Response | None = None
    payload = b""
    error: str | None = None
    parsed: dict[str, Any] = {}
    try:
        response = await http_client.get(service_url, params=params)
        payload = response.content
        if response.is_success:
            parsed = parse_wms_capabilities(payload)
            status = "AVAILABLE"
        else:
            status = "BLOCKED_UPSTREAM"
            error = f"HTTP {response.status_code}"
    except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
        status = (
            "INVALID_CAPABILITIES"
            if response is not None and response.is_success
            else "BLOCKED_UPSTREAM"
        )
        error = f"{type(exc).__name__}: {exc}"

    content_sha256 = hashlib.sha256(payload).hexdigest() if payload else None
    source_record_id = None
    if payload:
        raw_ref = await raw_store.put(
            source="inspire",
            resource="ktimatologio_wms_capabilities",
            partition_key="service=ktimatologio",
            payload=payload,
        )
        existing = (
            await conn.execute(
                sa.select(source_records.c.id).where(
                    source_records.c.source_system == "INSPIRE",
                    source_records.c.resource_type == "WMS_CAPABILITIES",
                    source_records.c.content_sha256 == raw_ref.content_sha256,
                )
            )
        ).scalar()
        source_record_id = existing
        if source_record_id is None:
            source_record_id = uuid.uuid4()
            await conn.execute(
                source_records.insert().values(
                    id=source_record_id,
                    source_system="INSPIRE",
                    resource_type="WMS_CAPABILITIES",
                    source_native_id=service_url,
                    content_sha256=raw_ref.content_sha256,
                    payload_uri=raw_ref.payload_uri,
                    fetched_at=checked_at,
                    http_status=response.status_code if response else None,
                    license_code="UNCONFIRMED",
                    parse_status="PARSED" if status == "AVAILABLE" else "FAILED",
                    parse_error={"message": error} if error else None,
                )
            )

    await conn.execute(
        pg_insert(external_datasets)
        .values(
            id=uuid.uuid4(),
            catalog_source="INSPIRE",
            catalog_dataset_id="KTIMATOLOGIO_WMS",
            title="Γεωπύλη INSPIRE Ελληνικού Κτηματολογίου",
            publisher="Ελληνικό Κτηματολόγιο",
            license_code=parsed.get("access_constraints") or "UNCONFIRMED",
            resource_type="WMS",
            resource_url=service_url,
            last_seen_at=checked_at,
            ingestion_status="ONBOARDED" if status == "AVAILABLE" else "BLOCKED",
            adapter_name="ogc_wms",
            config={"service_type": "WMS", "cadastral_parcels_in_scope": False},
        )
        .on_conflict_do_update(
            index_elements=[
                external_datasets.c.catalog_source,
                external_datasets.c.catalog_dataset_id,
            ],
            set_={
                "license_code": parsed.get("access_constraints") or "UNCONFIRMED",
                "resource_url": service_url,
                "last_seen_at": checked_at,
                "ingestion_status": (
                    "ONBOARDED" if status == "AVAILABLE" else "BLOCKED"
                ),
            },
        )
    )
    update_values = {
        "catalog_source": "KTIMATOLOGIO_INSPIRE",
        "service_type": "WMS",
        "service_version": parsed.get("version"),
        "title": parsed.get("title"),
        "provider_name": parsed.get("provider_name"),
        "access_constraints": parsed.get("access_constraints"),
        "fees": parsed.get("fees"),
        "formats": parsed.get("formats", []),
        "layers": parsed.get("layers", []),
        "status": status,
        "http_status": response.status_code if response else None,
        "content_sha256": content_sha256,
        "source_record_id": source_record_id,
        "last_error": {"message": error} if error else None,
        "checked_at": checked_at,
    }
    if status == "AVAILABLE":
        update_values["last_available_at"] = checked_at
    await conn.execute(
        pg_insert(spatial_service_capabilities)
        .values(
            id=uuid.uuid4(),
            service_url=service_url,
            **update_values,
        )
        .on_conflict_do_update(
            index_elements=[spatial_service_capabilities.c.service_url],
            set_=update_values,
        )
    )
    await conn.commit()
    return CapabilityCheckResult(
        status=status,
        http_status=response.status_code if response else None,
        layer_count=len(parsed.get("layers", [])),
        formats=tuple(parsed.get("formats", [])),
        error=error,
    )
