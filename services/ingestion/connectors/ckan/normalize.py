"""Population-by-municipality/NUTS and regional-economic-indicator
denominator parsing (§22.2's "πληθυσμός ανά δήμο ή NUTS" and "περιφερειακοί
οικονομικοί δείκτες" datasets — both feed `geo_denominators`, §22.3's
per-capita/per-indicator metrics).

This is one of several planned per-dataset adapters (administrative
boundaries/schools/hospitals are the others — see `boundaries.py` and
`facilities.py`) — each needs its own field mapping the same way this one
does. Column names below are a best-effort guess — no sample file from
data.gov.gr was available at build time; fix here once one is confirmed
(docs/source-contracts/ckan-datagov.md).

`normalize_metric_csv()` is the generic entry point: population and
regional-indicator files share the exact same shape (a geography code column
+ one numeric value column per row), differing only in which column name
holds the value — `normalize_population_csv()` is a thin wrapper fixing
that to the population-specific column names, kept for backward
compatibility with existing callers/tests.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_VALUE_FIELD_CANDIDATES = ("population", "plithysmos", "value")


@dataclass(frozen=True)
class NormalizedMetricRow:
    municipality_code: str | None
    nuts_code: str | None
    value: Decimal


# kept as an alias — existing callers (db_writer.py, tests) import this name
NormalizedPopulationRow = NormalizedMetricRow


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


def normalize_metric_csv(
    csv_bytes: bytes, *, value_field_candidates: tuple[str, ...] = DEFAULT_VALUE_FIELD_CANDIDATES
) -> list[NormalizedMetricRow]:
    """Rows with no usable geography code or no parseable value are
    silently skipped, not raised — a file with a handful of malformed rows
    shouldn't block ingesting the rest of it (contrast with
    `source_records.parse_status`-level failures elsewhere, which are
    whole-payload, not per-row)."""
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[NormalizedMetricRow] = []
    for raw_row in reader:
        municipality_code = _first(raw_row, "kallikratis_code", "municipality_code", "dimos_code")
        nuts_code = _first(raw_row, "nuts_code", "nuts3", "nuts")
        value = _to_decimal(_first(raw_row, *value_field_candidates))
        if value is None or (municipality_code is None and nuts_code is None):
            continue
        rows.append(NormalizedMetricRow(municipality_code=municipality_code, nuts_code=nuts_code, value=value))
    return rows


def normalize_population_csv(csv_bytes: bytes) -> list[NormalizedMetricRow]:
    return normalize_metric_csv(csv_bytes, value_field_candidates=DEFAULT_VALUE_FIELD_CANDIDATES)
