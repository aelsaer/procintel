-- Fine-grained place-of-performance enrichment.
--
-- Ingestion only enqueues work. A separate worker extracts municipalities,
-- regional units and addresses from source payloads/document text, then
-- geocodes them without holding up the source connector.

ALTER TABLE act_locations
    ADD COLUMN IF NOT EXISTS municipality_name TEXT,
    ADD COLUMN IF NOT EXISTS regional_unit_name TEXT,
    ADD COLUMN IF NOT EXISTS region_name TEXT,
    ADD COLUMN IF NOT EXISTS country_code CHAR(2),
    ADD COLUMN IF NOT EXISTS location_kind TEXT NOT NULL DEFAULT 'PERFORMANCE',
    ADD COLUMN IF NOT EXISTS granularity TEXT,
    ADD COLUMN IF NOT EXISTS extraction_method TEXT,
    ADD COLUMN IF NOT EXISTS geocode_provider TEXT,
    ADD COLUMN IF NOT EXISTS confidence NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS enrichment_job_id UUID,
    ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_act_locations_municipality
    ON act_locations (municipality_name);
CREATE INDEX IF NOT EXISTS ix_act_locations_regional_unit
    ON act_locations (regional_unit_name);
CREATE INDEX IF NOT EXISTS ix_act_locations_precise_geom
    ON act_locations USING gist (geom)
    WHERE geom IS NOT NULL;

CREATE TABLE IF NOT EXISTS geospatial_enrichment_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id              UUID NOT NULL REFERENCES procurement_acts(id) ON DELETE CASCADE,
    source_record_id    UUID NOT NULL REFERENCES source_records(id),
    status              TEXT NOT NULL DEFAULT 'QUEUED',
        -- QUEUED | RUNNING | SUCCEEDED | PARTIAL | NO_LOCATION | SUPERSEDED | FAILED
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    available_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at           TIMESTAMPTZ,
    locked_by           TEXT,
    last_error          JSONB,
    result              JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    UNIQUE (act_id, source_record_id)
);

CREATE INDEX IF NOT EXISTS ix_geo_jobs_pending
    ON geospatial_enrichment_jobs (available_at, created_at)
    WHERE status IN ('QUEUED', 'FAILED');

CREATE TABLE IF NOT EXISTS geocoding_cache (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider            TEXT NOT NULL,
    query_hash          TEXT NOT NULL,
    query_normalized    TEXT NOT NULL,
    status              TEXT NOT NULL, -- FOUND | NOT_FOUND
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    display_name        TEXT,
    municipality_name   TEXT,
    regional_unit_name  TEXT,
    region_name         TEXT,
    postal_code         TEXT,
    country_code        CHAR(2),
    precision           TEXT,
    raw_response        JSONB,
    hit_count           INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, query_hash)
);

-- Local, refreshable populated-place/administrative-unit gazetteer. GeoNames
-- country dumps are CC BY 4.0 and avoid one provider request per act.
CREATE TABLE IF NOT EXISTS geocoding_places (
    geoname_id             BIGINT PRIMARY KEY,
    country_code           CHAR(2) NOT NULL,
    name                   TEXT NOT NULL,
    normalized_names       TEXT[] NOT NULL,
    admin_name_1           TEXT,
    admin_code_1           TEXT,
    admin_name_2           TEXT,
    admin_code_2           TEXT,
    admin_name_3           TEXT,
    admin_code_3           TEXT,
    feature_class          CHAR(1) NOT NULL,
    feature_code           TEXT NOT NULL,
    population             BIGINT,
    latitude               DOUBLE PRECISION NOT NULL,
    longitude              DOUBLE PRECISION NOT NULL,
    source_name            TEXT NOT NULL,
    source_version         TEXT NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_geocoding_places_country
    ON geocoding_places (country_code);
CREATE INDEX IF NOT EXISTS ix_geocoding_places_names
    ON geocoding_places USING gin (normalized_names);

CREATE OR REPLACE FUNCTION enqueue_act_geospatial_enrichment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE', 'AWARD', 'CONTRACT', 'TED_NOTICE') THEN
        INSERT INTO geospatial_enrichment_jobs (act_id, source_record_id)
        VALUES (NEW.id, NEW.source_record_id)
        ON CONFLICT (act_id, source_record_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enqueue_act_geospatial_enrichment ON procurement_acts;
CREATE TRIGGER trg_enqueue_act_geospatial_enrichment
AFTER INSERT OR UPDATE OF source_record_id ON procurement_acts
FOR EACH ROW EXECUTE FUNCTION enqueue_act_geospatial_enrichment();

-- Documents often contain a more precise place than the API payload. Requeue
-- the current act after extracted page text changes; the unique key collapses
-- all pages in one document transaction into a single job.
CREATE OR REPLACE FUNCTION requeue_document_geospatial_enrichment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO geospatial_enrichment_jobs (act_id, source_record_id)
    SELECT d.act_id, a.source_record_id
    FROM documents d
    JOIN procurement_acts a ON a.id = d.act_id
    WHERE d.id = NEW.document_id
      AND a.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE', 'AWARD', 'CONTRACT', 'TED_NOTICE')
    ON CONFLICT (act_id, source_record_id) DO UPDATE
    SET status = 'QUEUED',
        available_at = now(),
        locked_at = NULL,
        locked_by = NULL,
        last_error = NULL,
        finished_at = NULL;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_requeue_document_geospatial_enrichment ON document_pages;
CREATE TRIGGER trg_requeue_document_geospatial_enrichment
AFTER INSERT OR UPDATE OF text ON document_pages
FOR EACH ROW EXECUTE FUNCTION requeue_document_geospatial_enrichment();
