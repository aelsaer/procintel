-- 20_product_workflows.sql
-- Tenant-persisted commercial workflows and operational history.
-- Spec refs: description.txt sections 31, 32, 38-40 and 43.

CREATE TABLE business_profiles (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL UNIQUE REFERENCES tenants(id),
    company_name            TEXT,
    description             TEXT NOT NULL DEFAULT '',
    cpv_prefixes            TEXT[] NOT NULL DEFAULT '{}',
    keywords                TEXT[] NOT NULL DEFAULT '{}',
    nuts_codes              TEXT[] NOT NULL DEFAULT '{}',
    municipality            TEXT,
    buyer_types             TEXT[] NOT NULL DEFAULT '{}',
    procedure_types         TEXT[] NOT NULL DEFAULT '{}',
    amount_min              NUMERIC(18,2),
    amount_max              NUMERIC(18,2),
    classification_version  INTEGER NOT NULL DEFAULT 1,
    classified_at           TIMESTAMPTZ,
    created_by              UUID REFERENCES users(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (amount_min IS NULL OR amount_min >= 0),
    CHECK (amount_max IS NULL OR amount_max >= 0),
    CHECK (amount_min IS NULL OR amount_max IS NULL OR amount_min <= amount_max)
);

CREATE TABLE business_profile_terms (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
    term_type       TEXT NOT NULL,
    value           TEXT NOT NULL,
    label           TEXT NOT NULL,
    confidence      NUMERIC(5,4) NOT NULL,
    reason          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'RULE',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile_id, term_type, value),
    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE TABLE workspace_watch_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    user_id         UUID NOT NULL REFERENCES users(id),
    object_type     TEXT NOT NULL,
    object_id       UUID NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, object_type, object_id),
    CHECK (object_type IN ('BUYER', 'COMPETITOR', 'SUPPLIER'))
);

ALTER TABLE saved_searches ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE opportunity_pipeline_items ADD COLUMN IF NOT EXISTS assigned_user_id UUID REFERENCES users(id);
ALTER TABLE opportunity_pipeline_items ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'MEDIUM';
ALTER TABLE opportunity_pipeline_items ADD COLUMN IF NOT EXISTS expected_value NUMERIC(18,2);
ALTER TABLE opportunity_pipeline_items ADD COLUMN IF NOT EXISTS next_action TEXT;
ALTER TABLE opportunity_pipeline_items ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ;
ALTER TABLE opportunity_pipeline_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE opportunity_pipeline_history (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_item_id    UUID NOT NULL REFERENCES opportunity_pipeline_items(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    from_stage          TEXT,
    to_stage            TEXT NOT NULL,
    changed_by          UUID NOT NULL REFERENCES users(id),
    changed_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE notes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Europe/Athens';
ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS digest_time TIME NOT NULL DEFAULT '08:00';
ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;

CREATE TABLE alert_digest_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    alert_rule_id       UUID REFERENCES alert_rules(id) ON DELETE SET NULL,
    schedule            TEXT NOT NULL,
    period_started_at   TIMESTAMPTZ NOT NULL,
    period_ended_at     TIMESTAMPTZ NOT NULL,
    event_count         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    channels            TEXT[] NOT NULL DEFAULT '{}',
    error               JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at        TIMESTAMPTZ,
    CHECK (status IN ('PENDING', 'RUNNING', 'DELIVERED', 'PARTIAL', 'FAILED'))
);

CREATE TABLE export_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    user_id         UUID NOT NULL REFERENCES users(id),
    export_type     TEXT NOT NULL,
    format          TEXT NOT NULL,
    filters         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'PENDING',
    row_count       INTEGER,
    file_name       TEXT,
    mime_type       TEXT,
    storage_path    TEXT,
    error           JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    CHECK (format IN ('CSV', 'XLSX')),
    CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'EXPIRED'))
);

CREATE TABLE opportunity_score_jobs (
    tenant_id       UUID PRIMARY KEY REFERENCES tenants(id),
    status          TEXT NOT NULL DEFAULT 'QUEUED',
    reason          TEXT NOT NULL,
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error           JSONB,
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'))
);

CREATE OR REPLACE FUNCTION enqueue_opportunity_scoring_for_new_act()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO opportunity_score_jobs (tenant_id, status, reason, requested_at, error)
    SELECT tenant_id, 'QUEUED', 'PROCUREMENT_CHANGED', now(), NULL
    FROM business_profiles
    ON CONFLICT (tenant_id) DO UPDATE SET
        status = 'QUEUED', reason = EXCLUDED.reason,
        requested_at = EXCLUDED.requested_at, error = NULL;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_procurement_act_score_queue
AFTER INSERT OR UPDATE OF title, amount_gross, publication_date, end_date, is_current
ON procurement_acts FOR EACH ROW
EXECUTE FUNCTION enqueue_opportunity_scoring_for_new_act();

CREATE INDEX ix_business_profile_terms_profile ON business_profile_terms (profile_id, is_active);
CREATE INDEX ix_watch_items_tenant ON workspace_watch_items (tenant_id, object_type, created_at DESC);
CREATE INDEX ix_pipeline_history_item ON opportunity_pipeline_history (pipeline_item_id, changed_at DESC);
CREATE INDEX ix_alert_digest_runs_tenant ON alert_digest_runs (tenant_id, created_at DESC);
CREATE INDEX ix_export_jobs_tenant ON export_jobs (tenant_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON business_profiles, business_profile_terms,
    workspace_watch_items, opportunity_pipeline_history, alert_digest_runs, export_jobs,
    opportunity_score_jobs TO procintel_app;

DO $$
DECLARE
    direct_table TEXT;
BEGIN
    FOREACH direct_table IN ARRAY ARRAY[
        'saved_searches', 'opportunity_pipeline_items', 'notes', 'tags',
        'business_profiles', 'workspace_watch_items', 'opportunity_pipeline_history',
        'alert_digest_runs', 'export_jobs', 'opportunity_score_jobs'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', direct_table);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', direct_table);
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname = 'public' AND tablename = direct_table AND policyname = 'tenant_isolation'
        ) THEN
            EXECUTE format(
                'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)::uuid) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true)::uuid)',
                direct_table
            );
        END IF;
    END LOOP;
END
$$;

ALTER TABLE business_profile_terms ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_profile_terms FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON business_profile_terms
    USING (profile_id IN (
        SELECT id FROM business_profiles
        WHERE tenant_id = current_setting('app.tenant_id', true)::uuid
    ));

ALTER TABLE tag_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE tag_links FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tag_links
    USING (tag_id IN (
        SELECT id FROM tags
        WHERE tenant_id = current_setting('app.tenant_id', true)::uuid
    ));
