-- 07_data_quality.sql
-- Data quality issue tracking / quarantine (§33). Feeds the ingestion
-- freshness dashboard and blocks canonicalization on BLOCKING severities.
-- Spec refs: description.txt §33.

CREATE TABLE data_quality_issues (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_record_id      UUID REFERENCES source_records(id),
    object_type              TEXT,
    object_id                  UUID,
    issue_code                    TEXT NOT NULL,
        -- e.g. INVALID_AFM_CHECKSUM, DUPLICATE_ADAM, END_BEFORE_START,
        -- UNRESOLVED_CPV_CODE, SCHEMA_DRIFT, IDENTIFIER_CONFLICT
    severity                        TEXT NOT NULL,          -- INFO | WARNING | ERROR | BLOCKING
    details                            JSONB,
    status                                TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN | ACKNOWLEDGED | RESOLVED | WONT_FIX
    created_at                             TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at                              TIMESTAMPTZ
);

CREATE INDEX ix_dq_issues_open ON data_quality_issues (severity, status) WHERE status = 'OPEN';
CREATE INDEX ix_dq_issues_object ON data_quality_issues (object_type, object_id);
CREATE INDEX ix_dq_issues_source_record ON data_quality_issues (source_record_id);
