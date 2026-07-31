-- Auditable INSPIRE network-service capabilities. Actual cadastral parcels
-- remain out of scope; this stores service health/licence/layer metadata.

CREATE TABLE IF NOT EXISTS spatial_service_capabilities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_source      TEXT NOT NULL,
    service_url         TEXT NOT NULL UNIQUE,
    service_type        TEXT NOT NULL,
    service_version     TEXT,
    title               TEXT,
    provider_name       TEXT,
    access_constraints  TEXT,
    fees                TEXT,
    formats             TEXT[] NOT NULL DEFAULT '{}',
    layers              JSONB NOT NULL DEFAULT '[]'::jsonb,
    status              TEXT NOT NULL,
        -- AVAILABLE | DEGRADED | BLOCKED_UPSTREAM | INVALID_CAPABILITIES
    http_status         INTEGER,
    content_sha256      TEXT,
    source_record_id    UUID REFERENCES source_records(id),
    last_error          JSONB,
    checked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_available_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_spatial_capabilities_status
    ON spatial_service_capabilities (catalog_source, status, checked_at DESC);
