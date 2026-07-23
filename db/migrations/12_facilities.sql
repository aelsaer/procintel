-- 12_facilities.sql
-- Schools/hospitals ("βασικά στοιχεία", §22.2) — feeds §22.3's per-facility
-- derived metrics ("project value per student", "health procurement value
-- per bed"). A generic facility_type + capacity_metric/capacity_value pair
-- (STUDENTS for schools, BEDS for hospitals) rather than separate tables
-- per facility type, mirroring geo_denominators' metric_name pattern.
-- geom is nullable — a facility record is still useful without a point
-- location (name/capacity alone still support the per-capita metrics
-- above), unlike administrative_boundaries where the shape *is* the point.
-- Spec refs: description.txt §22.2, §22.3.

CREATE TABLE facilities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    facility_type       TEXT NOT NULL,               -- SCHOOL | HOSPITAL
    external_dataset_id UUID REFERENCES external_datasets(id),
    code                TEXT,
    name                TEXT,
    nuts_code           TEXT,
    municipality_code   TEXT,
    capacity_metric     TEXT,                        -- STUDENTS | BEDS
    capacity_value      NUMERIC(12, 0),
    geom                geometry(Point, 4326),
    source_record_id    UUID REFERENCES source_records(id)
);

CREATE INDEX ix_facilities_geom ON facilities USING gist (geom);
CREATE INDEX ix_facilities_type ON facilities (facility_type);
CREATE INDEX ix_facilities_dataset ON facilities (external_dataset_id);
