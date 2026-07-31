"""Schools/hospitals ("βασικά στοιχεία", §22.2) facility adapter — feeds
§22.3's per-facility derived metrics ("project value per student", "health
procurement value per bed"). One `facility_type` (SCHOOL/HOSPITAL) +
`capacity_metric`/`capacity_value` pair rather than separate tables,
mirroring `geo_denominators.metric_name`'s pattern in the other CKAN
adapters.

The school mapping includes the live data.gov.gr
`minedu_students_school` schema (`school_name` plus male/female registered
student columns). Generic aliases remain available for other onboarded
school and hospital CSV resources.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_CAPACITY_FIELD_CANDIDATES = ("capacity", "students", "beds", "capacity_value")
STUDENT_COMPONENT_FIELDS = ("registered_students_boys", "registered_students_girls")


@dataclass(frozen=True)
class NormalizedFacility:
    code: str | None
    name: str | None
    nuts_code: str | None
    municipality_code: str | None
    capacity_value: Decimal | None
    latitude: Decimal | None
    longitude: Decimal | None


def _first(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value).strip()
    return None


def _to_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.strip().replace(",", ""))
    except InvalidOperation:
        return None


def normalize_facilities_csv(
    csv_bytes: bytes, *, capacity_field_candidates: tuple[str, ...] = DEFAULT_CAPACITY_FIELD_CANDIDATES
) -> list[NormalizedFacility]:
    """Rows with neither a code nor a name are skipped (nothing usable to
    store) — otherwise a facility is kept even with no coordinates or no
    capacity value, since a bare name/location is still real data, not
    nothing (contrast with population/regional-indicator rows, which are
    useless without a value)."""
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[NormalizedFacility] = []
    for raw_row in reader:
        code = _first(raw_row, "code", "facility_code", "school_code", "hospital_code")
        name = _first(raw_row, "name", "facility_name", "school_name", "hospital_name")
        if code is None and name is None:
            continue
        capacity_value = _to_decimal(_first(raw_row, *capacity_field_candidates))
        if capacity_value is None:
            component_values = [
                value
                for field in STUDENT_COMPONENT_FIELDS
                if (value := _to_decimal(_first(raw_row, field))) is not None
            ]
            if component_values:
                capacity_value = sum(component_values, Decimal("0"))
        rows.append(
            NormalizedFacility(
                code=code,
                name=name,
                nuts_code=_first(raw_row, "nuts_code", "nuts3", "nuts"),
                municipality_code=_first(raw_row, "kallikratis_code", "municipality_code", "dimos_code"),
                capacity_value=capacity_value,
                latitude=_to_decimal(_first(raw_row, "lat", "latitude")),
                longitude=_to_decimal(_first(raw_row, "lon", "lng", "longitude")),
            )
        )
    return rows
