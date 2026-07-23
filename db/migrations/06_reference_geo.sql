-- 06_reference_geo.sql
-- Reference/master data: CPV hierarchy and geographic layers (NUTS,
-- administrative boundaries from INSPIRE/data.gov.gr). These are looked up
-- by, not hard-FK'd from, act_cpv_codes/act_locations/entity_addresses —
-- ingestion must not hard-fail on a code the reference table hasn't caught
-- up with yet; the data-quality layer (07) flags the gap instead.
-- Spec refs: description.txt §3.7, §7.2, §22.

CREATE TABLE cpv_codes (
    code                TEXT PRIMARY KEY,             -- full 8-digit code, e.g. '72000000'
    check_digit         TEXT,
    prefix_2            TEXT NOT NULL,
    prefix_3            TEXT NOT NULL,
    prefix_4            TEXT NOT NULL,
    prefix_5            TEXT NOT NULL,
    parent_code         TEXT REFERENCES cpv_codes(code),
    description_el      TEXT,
    description_en      TEXT
);

CREATE INDEX ix_cpv_prefix2 ON cpv_codes (prefix_2);
CREATE INDEX ix_cpv_prefix4 ON cpv_codes (prefix_4);

CREATE TABLE nuts_areas (
    code                    TEXT PRIMARY KEY,
    level                     SMALLINT NOT NULL,          -- 0=country .. 3
    name_el                     TEXT,
    name_en                       TEXT,
    classification_version          TEXT NOT NULL,          -- e.g. 'NUTS-2021'
    parent_code                       TEXT REFERENCES nuts_areas(code),
    geom                                 geometry(MultiPolygon, 4326)
);

CREATE INDEX ix_nuts_areas_geom ON nuts_areas USING gist (geom);
CREATE INDEX ix_nuts_areas_parent ON nuts_areas (parent_code);

-- ---------------------------------------------------------------------------
-- administrative_boundaries: first geospatial slice per §3.7 — municipal/
-- regional boundaries, postal codes, selected environmental/thematic zones.
-- Cadastral parcels are explicitly out of scope for v1 (§3.7).
-- ---------------------------------------------------------------------------
CREATE TABLE administrative_boundaries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    boundary_type          TEXT NOT NULL,               -- MUNICIPALITY | REGION | REGIONAL_UNIT | POSTAL_CODE | ENVIRONMENTAL_ZONE
    external_dataset_id      UUID REFERENCES external_datasets(id),
    code                        TEXT,
    name                          TEXT,
    nuts_code                       TEXT REFERENCES nuts_areas(code),
    geom                               geometry(MultiPolygon, 4326) NOT NULL,
    valid_from                           TIMESTAMPTZ,
    valid_to                               TIMESTAMPTZ,
    source_record_id                         UUID REFERENCES source_records(id)
);

CREATE INDEX ix_admin_boundaries_geom ON administrative_boundaries USING gist (geom);
CREATE INDEX ix_admin_boundaries_type ON administrative_boundaries (boundary_type);

-- ---------------------------------------------------------------------------
-- geo_denominators: population and other denominators behind the §22.3
-- per-capita metrics (contracts per resident, project value per student,
-- health procurement per bed). Always carries the year and source so the
-- metric can cite "which denominator, which year, which methodology".
-- ---------------------------------------------------------------------------
CREATE TABLE geo_denominators (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_name             TEXT NOT NULL,              -- POPULATION | STUDENTS | HOSPITAL_BEDS | ...
    nuts_code                  TEXT REFERENCES nuts_areas(code),
    municipality_code             TEXT,
    reference_year                  INTEGER NOT NULL,
    value                              NUMERIC(20,2) NOT NULL,
    external_dataset_id                  UUID REFERENCES external_datasets(id),
    source_record_id                        UUID REFERENCES source_records(id)
);

CREATE INDEX ix_geo_denominators_lookup ON geo_denominators (metric_name, nuts_code, reference_year);
