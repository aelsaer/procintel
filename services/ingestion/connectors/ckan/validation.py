"""Live resource-schema validation for onboarded CKAN datasets."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from packages.domain.tables import external_dataset_validations


@dataclass(frozen=True)
class DatasetValidation:
    schema_fingerprint: str
    detected_format: str
    columns: list[str]
    sample: Any
    status: str
    errors: list[str]


def validate_resource(
    content: bytes,
    *,
    adapter_name: str,
    required_column_groups: tuple[tuple[str, ...], ...] = (),
) -> DatasetValidation:
    errors: list[str] = []
    columns: list[str] = []
    sample: Any = None
    detected_format = "UNKNOWN"
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        columns = [value for value in reader.fieldnames or [] if value]
        sample = next(reader, None)
        detected_format = "CSV"
    else:
        if isinstance(document, dict) and document.get("type") == "FeatureCollection":
            detected_format = "GEOJSON"
            features = document.get("features") or []
            first = features[0] if features else {}
            sample = first.get("properties") if isinstance(first, dict) else None
            columns = sorted((sample or {}).keys()) if isinstance(sample, dict) else []
            if not features:
                errors.append("FeatureCollection has no features")
            if adapter_name == "boundaries":
                geometry_type = (
                    ((first.get("geometry") or {}).get("type"))
                    if isinstance(first, dict)
                    else None
                )
                if geometry_type not in {"Polygon", "MultiPolygon"}:
                    errors.append("boundary resource has no Polygon/MultiPolygon geometry")
        else:
            detected_format = "JSON"
            errors.append("JSON resource is not a GeoJSON FeatureCollection")

    if adapter_name == "boundaries":
        if not set(columns).intersection(
            {"kalcode", "kallikratis_code", "code", "boundary_code"}
        ):
            errors.append("no supported boundary code column")
        if not set(columns).intersection({"lektiko", "name", "boundary_name"}):
            errors.append("no supported boundary name column")
    elif adapter_name in {"population", "metric", "facilities"} and not columns:
        errors.append("CSV resource has no header")
    column_set = set(columns)
    for group in required_column_groups:
        if not column_set.intersection(group):
            errors.append(f"none of the required columns are present: {', '.join(group)}")

    fingerprint_source = json.dumps(
        {"format": detected_format, "columns": sorted(columns)},
        sort_keys=True,
    ).encode("utf-8")
    return DatasetValidation(
        schema_fingerprint=hashlib.sha256(fingerprint_source).hexdigest(),
        detected_format=detected_format,
        columns=columns,
        sample=sample,
        status="VALID" if not errors else "INVALID",
        errors=errors,
    )


async def record_validation(
    conn: AsyncConnection,
    *,
    external_dataset_id: uuid.UUID,
    adapter_name: str,
    resource_url: str,
    validation: DatasetValidation,
) -> uuid.UUID:
    validation_id = uuid.uuid4()
    await conn.execute(
        external_dataset_validations.insert().values(
            id=validation_id,
            external_dataset_id=external_dataset_id,
            adapter_name=adapter_name,
            resource_url=resource_url,
            schema_fingerprint=validation.schema_fingerprint,
            detected_format=validation.detected_format,
            columns=validation.columns,
            sample=validation.sample,
            status=validation.status,
            errors=validation.errors,
        )
    )
    return validation_id
