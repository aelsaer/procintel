-- 26_bid_workspace.sql
-- Collaborative tender qualification and bid execution workflow.

CREATE TABLE IF NOT EXISTS bid_workspaces (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    process_id          UUID NOT NULL REFERENCES procurement_processes(id) ON DELETE CASCADE,
    owner_user_id       UUID REFERENCES users(id),
    status              TEXT NOT NULL DEFAULT 'QUALIFYING',
    decision            TEXT NOT NULL DEFAULT 'PENDING',
    decision_rationale  TEXT,
    decision_by         UUID REFERENCES users(id),
    decision_at         TIMESTAMPTZ,
    submission_due_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, process_id),
    CHECK (status IN ('QUALIFYING', 'PREPARING', 'REVIEW', 'SUBMITTED', 'WON', 'LOST', 'ARCHIVED')),
    CHECK (decision IN ('PENDING', 'BID', 'NO_BID', 'CONDITIONAL'))
);

CREATE TABLE IF NOT EXISTS bid_tasks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    title               TEXT NOT NULL,
    description         TEXT,
    assigned_user_id    UUID REFERENCES users(id),
    status              TEXT NOT NULL DEFAULT 'TODO',
    priority            TEXT NOT NULL DEFAULT 'MEDIUM',
    due_at              TIMESTAMPTZ,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('TODO', 'IN_PROGRESS', 'BLOCKED', 'DONE')),
    CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT'))
);

CREATE TABLE IF NOT EXISTS bid_requirements (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    requirement_type    TEXT NOT NULL DEFAULT 'OTHER',
    title               TEXT NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'UNREVIEWED',
    mandatory           BOOLEAN NOT NULL DEFAULT TRUE,
    evidence_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    evidence_page       INTEGER,
    source_excerpt      TEXT,
    owner_user_id       UUID REFERENCES users(id),
    due_at              TIMESTAMPTZ,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (requirement_type IN (
        'ELIGIBILITY', 'TECHNICAL', 'FINANCIAL', 'CERTIFICATE',
        'DELIVERABLE', 'DEADLINE', 'LEGAL', 'OTHER'
    )),
    CHECK (status IN ('UNREVIEWED', 'MET', 'PARTIAL', 'MISSING', 'NOT_APPLICABLE'))
);

CREATE INDEX IF NOT EXISTS ix_bid_tasks_workspace_status
    ON bid_tasks (bid_workspace_id, status, due_at);
CREATE INDEX IF NOT EXISTS ix_bid_requirements_workspace_status
    ON bid_requirements (bid_workspace_id, status, mandatory);

DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['bid_workspaces', 'bid_tasks', 'bid_requirements']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)::uuid) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true)::uuid)',
            table_name
        );
    END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON bid_workspaces, bid_tasks, bid_requirements
    TO procintel_app;
