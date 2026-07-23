-- 01_extensions_and_source_layer.sql
-- Immutable raw/source-record layer, ingestion run tracking, cursors, and the
-- external (CKAN/data.gov.gr) dataset registry.
-- Spec refs: description.txt §10, §13, §14, §22.1, §34-36, §45 (Βήμα 1).

CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- fuzzy/trigram name search
CREATE EXTENSION IF NOT EXISTS postgis;       -- geometry types

-- ---------------------------------------------------------------------------
-- source_records: append-only, one row per distinct payload version ever
-- fetched from a source. Never updated in place; superseded rows just get
-- is_latest = false. This is what makes reprocessing, schema-drift recovery
-- and "what did we know on date X" queries possible (§13.3, §14).
-- ---------------------------------------------------------------------------
CREATE TABLE source_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system       TEXT NOT NULL,              -- KHMDHS, DIAVGEIA, GEMI, ANAPTYXI, MEF, TED, VIES, CKAN, INSPIRE
    resource_type       TEXT NOT NULL,               -- e.g. contract, notice, decision, company, project
    source_native_id    TEXT,                        -- ADAM, ADA, VAT, MIS/OPS, TED notice id, etc. (raw)
    source_version      TEXT,
    content_sha256      TEXT NOT NULL,
    payload_uri         TEXT NOT NULL,                -- s3://raw/<source>/<resource>/...
    fetched_at          TIMESTAMPTZ NOT NULL,
    source_updated_at   TIMESTAMPTZ,
    http_status         INTEGER,
    schema_version      TEXT,
    license_code        TEXT,
    request_url_hash    TEXT,
    parse_status        TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING | PARSED | QUARANTINED | FAILED
    parse_error         JSONB,
    is_latest           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_source_record_hash
    ON source_records (source_system, resource_type, content_sha256);

CREATE INDEX ix_source_records_native_id
    ON source_records (source_system, resource_type, source_native_id)
    WHERE is_latest = TRUE;

CREATE INDEX ix_source_records_parse_status
    ON source_records (parse_status)
    WHERE parse_status IN ('QUARANTINED', 'FAILED');

-- ---------------------------------------------------------------------------
-- connector_runs: one row per orchestration run (a partition attempt), so
-- ingestion success rate, per-source freshness, and retry history are
-- queryable without grepping logs. Referenced by the ingestion Definition of
-- Done (§46) and the Στάδιο-1 "run tracking" deliverable (§44).
-- ---------------------------------------------------------------------------
CREATE TABLE connector_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system       TEXT NOT NULL,
    resource_type       TEXT NOT NULL,
    partition_key       TEXT NOT NULL,               -- e.g. "2025-01-01:2025-01-30" or a cursor token
    run_type            TEXT NOT NULL,                -- BACKFILL | INCREMENTAL | ADAMCHAIN | ENRICHMENT
    status               TEXT NOT NULL DEFAULT 'RUNNING', -- RUNNING | SUCCEEDED | FAILED | PARTIAL
    pages_fetched       INTEGER NOT NULL DEFAULT 0,
    records_fetched     INTEGER NOT NULL DEFAULT 0,
    records_upserted    INTEGER NOT NULL DEFAULT 0,
    rate_limit_hits     INTEGER NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    error               JSONB,
    triggered_by        TEXT                          -- SCHEDULE | MANUAL | BACKFILL_JOB | ALERT_JOB
);

CREATE INDEX ix_connector_runs_source_status
    ON connector_runs (source_system, resource_type, status, started_at DESC);

-- ---------------------------------------------------------------------------
-- source_cursors: watermark per (source, resource, partition). Only advances
-- after all pages are fetched, raw payload stored, staging completed and
-- failures recorded (§35).
-- ---------------------------------------------------------------------------
CREATE TABLE source_cursors (
    source_system       TEXT NOT NULL,
    resource_type       TEXT NOT NULL,
    partition_key        TEXT NOT NULL,
    cursor_value        JSONB NOT NULL,
    last_success_at      TIMESTAMPTZ,
    last_attempt_at      TIMESTAMPTZ,
    last_error           JSONB,
    PRIMARY KEY (source_system, resource_type, partition_key)
);

-- ---------------------------------------------------------------------------
-- external_datasets: CKAN/data.gov.gr dataset registry (§22.1). A catalogue
-- of enrichment sources, not an operational store in its own right.
-- ---------------------------------------------------------------------------
CREATE TABLE external_datasets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_source      TEXT NOT NULL,               -- DATA_GOV_GR | INSPIRE
    catalog_dataset_id  TEXT NOT NULL,
    title               TEXT NOT NULL,
    publisher           TEXT,
    license_code        TEXT,
    resource_type       TEXT,
    resource_url        TEXT,
    update_frequency    TEXT,
    last_seen_at        TIMESTAMPTZ,
    ingestion_status    TEXT NOT NULL DEFAULT 'NOT_ONBOARDED', -- NOT_ONBOARDED | ONBOARDED | DISABLED
    adapter_name        TEXT,
    config              JSONB,
    UNIQUE (catalog_source, catalog_dataset_id)
);
