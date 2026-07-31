-- Durable provider enrichment, truthful connector counters, and data-quality
-- issue idempotency for unattended ingestion.

ALTER TABLE connector_runs
    ADD COLUMN IF NOT EXISTS records_unchanged INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS records_failed INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS enrichment_succeeded INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS enrichment_failed INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS enrichment_deferred INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS metrics JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS enrichment_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider            TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL,
    object_type         TEXT,
    object_id           UUID,
    source_record_id    UUID REFERENCES source_records(id),
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'QUEUED',
        -- QUEUED | RUNNING | SUCCEEDED | FAILED | BLOCKED_CONFIG |
        -- BLOCKED_UPSTREAM | DEAD
    priority            SMALLINT NOT NULL DEFAULT 100,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 8,
    available_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at           TIMESTAMPTZ,
    locked_by           TEXT,
    last_error          JSONB,
    result              JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    UNIQUE (provider, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_enrichment_jobs_pending
    ON enrichment_jobs (priority, available_at, created_at)
    WHERE status IN ('QUEUED', 'FAILED');

CREATE INDEX IF NOT EXISTS ix_enrichment_jobs_object
    ON enrichment_jobs (object_type, object_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_data_quality_open_object_issue
    ON data_quality_issues (
        COALESCE(object_type, ''),
        COALESCE(object_id, '00000000-0000-0000-0000-000000000000'::uuid),
        issue_code
    )
    WHERE status IN ('OPEN', 'ACKNOWLEDGED');
