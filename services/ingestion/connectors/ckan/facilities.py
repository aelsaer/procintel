"""Schools/hospitals ("βασικά στοιχεία", §22.2) facility adapter — feeds
§22.3's per-facility derived metrics ("project value per student", "health
procurement value per bed"). One `facility_type` (SCHOOL/HOSPITAL) +
`capacity_metric`/`capacity_value` pair rather than separate tables,
mirroring `geo_denominators.metric_name`'s pattern in the other CKAN
adapters.

Column names are a best-effort guess — no sample school/hospital file from
data.gov.gr was available at build time; fix here once one is confirmed
(docs/source-contracts/ckan-datagov.md). A plain CSV with optional
`lat`/`lon` columns is assumed (simpler than GeoJSON for point facilities,
and standard-JSON/CSV is what data.gov.gr's own announcement lists as bulk
formats) — a facility with no coordinates still gets a row (name/capacity
alone already support the per-capita metrics above), just no `geom`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_CAPACITY_FIELD_CANDIDATES = ("capacity", "students", "beds", "capacity_value")


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
        name = _first(raw_row, "name", "facility_name")
        if code is None and name is None:
            continue
        rows.append(
            NormalizedFacility(
                code=code,
                name=name,
                nuts_code=_first(raw_row, "nuts_code", "nuts3", "nuts"),
                municipality_code=_first(raw_row, "kallikratis_code", "municipality_code", "dimos_code"),
                capacity_value=_to_decimal(_first(raw_row, *capacity_field_candidates)),
                latitude=_to_decimal(_first(raw_row, "lat", "latitude")),
                longitude=_to_decimal(_first(raw_row, "lon", "lng", "longitude")),
            )
        )
    return rows
