-- 25_relevance_feedback.sql
-- Tenant-controlled exclusions and labelled relevance feedback.

ALTER TABLE business_profiles
    ADD COLUMN IF NOT EXISTS excluded_cpv_prefixes TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE business_profiles
    ADD COLUMN IF NOT EXISTS excluded_keywords TEXT[] NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS opportunity_relevance_feedback (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    user_id         UUID NOT NULL REFERENCES users(id),
    process_id      UUID NOT NULL REFERENCES procurement_processes(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, process_id),
    CHECK (label IN ('RELEVANT', 'IRRELEVANT'))
);

CREATE INDEX IF NOT EXISTS ix_relevance_feedback_tenant_label
    ON opportunity_relevance_feedback (tenant_id, label, updated_at DESC);

ALTER TABLE opportunity_relevance_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE opportunity_relevance_feedback FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON opportunity_relevance_feedback;
CREATE POLICY tenant_isolation ON opportunity_relevance_feedback
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON opportunity_relevance_feedback TO procintel_app;
