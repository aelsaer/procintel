# connectors/ckan

Generic CKAN Action API connector for data.gov.gr (spec §3.6, §22).
`client.py` implements the three operations description.txt names
explicitly — `package_search`, `package_show`, `resource_search` — plus a
plain resource downloader, generically for any CKAN dataset. Every onboarded
dataset gets its own row in `external_datasets` (`registry.py`, idempotent
upsert on `(catalog_source, catalog_dataset_id)`) plus its own per-dataset
adapter, since each dataset's actual file format/field names differ.

**Implemented: all six §22.2 dataset types.**

- **Population + regional economic indicators**
  (`normalize.py::normalize_metric_csv`, `db_writer.py::ingest_metric_dataset`)
  — writes `geo_denominators` rows behind the §22.3 per-capita metrics
  ("public contracts per resident" and friends). Population
  (`normalize_population_csv`/`ingest_population_dataset`) is a thin
  wrapper fixing the metric name/value-column to the population-specific
  case; any other regional indicator (GDP per capita, unemployment rate,
  ...) uses the generic form with its own `metric_name` and value column.
- **Administrative boundaries** (`boundaries.py::normalize_boundaries_geojson`,
  `geo.py::geojson_to_multipolygon_wkt`, `db_writer.py::ingest_boundaries_dataset`)
  — writes `administrative_boundaries` rows with real PostGIS geometry via
  GeoAlchemy2 (the `geoalchemy2` package, added as a project dependency
  specifically for this — see `packages/domain/tables.py`'s module
  docstring). GeoJSON is assumed as the resource format (data.gov.gr's own
  bulk-download formats are CSV/JSON/XML/XLSX; GeoJSON is standard JSON and
  needs no Shapefile-parsing dependency) — confirm against the live
  dataset before relying on this. A single `Polygon` feature is wrapped as
  a one-member `MultiPolygon` to satisfy the column's type
  (`geometry(MultiPolygon, 4326) NOT NULL`); cadastral parcels remain
  explicitly out of scope (§3.7). `boundary_type` covers all five values
  from the DDL comment (`MUNICIPALITY`/`REGION`/`REGIONAL_UNIT`/
  `POSTAL_CODE`/`ENVIRONMENTAL_ZONE`) — it's a pure passthrough string with
  no special-casing, so "environmental layers" (§22.2) needed no new code,
  just `--boundary-type ENVIRONMENTAL_ZONE`.
- **Schools/hospitals** (`facilities.py::normalize_facilities_csv`,
  `db_writer.py::ingest_facilities_dataset`) — writes `facilities` rows
  (new table, `db/migrations/12_facilities.sql`) with an optional PostGIS
  `Point` geometry (nullable — a facility with a name/capacity but no
  coordinates is still real, useful data) and a generic
  `capacity_metric`/`capacity_value` pair (`STUDENTS` for schools, `BEDS`
  for hospitals) behind §22.3's "project value per student"/"health
  procurement value per bed" metrics. A plain CSV with optional `lat`/`lon`
  columns is assumed, not GeoJSON — simpler for point facilities, and one
  of data.gov.gr's own confirmed bulk formats.

Every adapter is a whole-dataset snapshot (one file = the entire current
state for that dataset scope), not a stream of individually-identifiable
records, so a changed file (different content hash) replaces every row for
that scope (`external_dataset_id` + `reference_year`/`boundary_type`/
`facility_type`) wholesale rather than upserting row by row.

Standalone CLI (like TED's — nothing on the ΚΗΜΔΗΣ side triggers a catalog
sync):

```
python -m services.ingestion.connectors.ckan.cli sync-population \
    --dataset-id <ckan-dataset-slug> --reference-year 2021

python -m services.ingestion.connectors.ckan.cli sync-metric \
    --dataset-id <ckan-dataset-slug> --metric-name GDP_PER_CAPITA \
    --reference-year 2024 [--value-field gdpPerCapita]

python -m services.ingestion.connectors.ckan.cli sync-boundaries \
    --dataset-id <ckan-dataset-slug> --boundary-type MUNICIPALITY

python -m services.ingestion.connectors.ckan.cli sync-facilities \
    --dataset-id <ckan-dataset-slug> --facility-type SCHOOL \
    --capacity-metric STUDENTS [--capacity-field students]
```

Onboarding (the commands above) is a one-time operator action, but keeping
a dataset fresh afterward isn't manual: `scheduled.py::refresh_due_ckan_datasets`
scans every `ONBOARDED` row and re-syncs whichever have gone stale
(`last_seen_at` older than 7 days by default), dispatching by
`adapter_name` back to the matching `_sync_*` function above with its
stored `config` unpacked as kwargs. It's wired into
`services/ingestion/orchestration/cli.py run-once`/`run-forever`
alongside the ΚΗΜΔΗΣ/TED scheduler sweep, deliberately as a *separate*
mechanism rather than a `ScheduledJob` — see
`services/ingestion/orchestration/README.md` for why whole-dataset
refreshes don't fit that date-windowed abstraction.

**Not yet implemented**: the Κτηματολόγιο INSPIRE Geoportal (also covered
by `docs/source-contracts/ckan-datagov.md`) is a separate API entirely, not
this generic CKAN client. `nuts_areas` reference data isn't loaded by
anything yet either — every adapter's `nuts_code` columns stay NULL unless
the source dataset's own properties happen to carry one.

See `docs/source-contracts/ckan-datagov.md`.
