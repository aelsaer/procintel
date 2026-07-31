-- 30_platform_completion.sql
-- Completes source-specific history, review, document-intelligence, and bid
-- collaboration storage that cannot be represented by the original MVP tables.

-- ---------------------------------------------------------------------------
-- Διαύγεια reference data and correction/version history.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS diavgeia_organizations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    uid                 TEXT NOT NULL UNIQUE,
    label               TEXT NOT NULL,
    abbreviation        TEXT,
    category            TEXT,
    active              BOOLEAN,
    vat_number          TEXT,
    website             TEXT,
    email               TEXT,
    raw                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS diavgeia_units (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    uid                 TEXT NOT NULL UNIQUE,
    organization_uid    TEXT NOT NULL,
    label               TEXT NOT NULL,
    category            TEXT,
    active              BOOLEAN,
    active_from         TIMESTAMPTZ,
    active_until        TIMESTAMPTZ,
    parent_uid          TEXT,
    unit_domains        TEXT[] NOT NULL DEFAULT '{}',
    raw                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_diavgeia_units_organization
    ON diavgeia_units (organization_uid);

CREATE TABLE IF NOT EXISTS diavgeia_signers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    uid                 TEXT NOT NULL,
    organization_uid    TEXT,
    first_name          TEXT,
    last_name           TEXT,
    active              BOOLEAN,
    active_from         TIMESTAMPTZ,
    active_until        TIMESTAMPTZ,
    raw                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (uid, organization_uid)
);

CREATE TABLE IF NOT EXISTS diavgeia_decision_versions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ada                     TEXT,
    version_id              TEXT NOT NULL UNIQUE,
    corrected_version_id    TEXT,
    status                  TEXT,
    submission_timestamp    TIMESTAMPTZ,
    issue_date              TIMESTAMPTZ,
    document_url            TEXT,
    document_checksum       TEXT,
    organization_uid        TEXT,
    unit_uids               TEXT[] NOT NULL DEFAULT '{}',
    signer_uids             TEXT[] NOT NULL DEFAULT '{}',
    raw                     JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id        UUID REFERENCES source_records(id),
    observed_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_diavgeia_versions_ada
    ON diavgeia_decision_versions (ada, observed_at DESC);

-- ---------------------------------------------------------------------------
-- ΓΕΜΗ full temporal profile and contact evidence.
-- ---------------------------------------------------------------------------
ALTER TABLE entity_company_snapshots
    ADD COLUMN IF NOT EXISTS address_line TEXT,
    ADD COLUMN IF NOT EXISTS postal_code TEXT,
    ADD COLUMN IF NOT EXISTS city TEXT,
    ADD COLUMN IF NOT EXISTS email TEXT,
    ADD COLUMN IF NOT EXISTS website TEXT,
    ADD COLUMN IF NOT EXISTS objective TEXT,
    ADD COLUMN IF NOT EXISTS last_status_change DATE,
    ADD COLUMN IF NOT EXISTS is_branch BOOLEAN,
    ADD COLUMN IF NOT EXISTS auto_registered BOOLEAN,
    ADD COLUMN IF NOT EXISTS kad_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS persons JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS capital JSONB,
    ADD COLUMN IF NOT EXISTS source_documents JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS entity_contacts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id           UUID NOT NULL REFERENCES entities(id),
    contact_type        TEXT NOT NULL,
    value_raw           TEXT NOT NULL,
    value_normalized    TEXT NOT NULL,
    source_record_id    UUID REFERENCES source_records(id),
    confidence          NUMERIC(5,4) NOT NULL DEFAULT 1,
    valid_from          TIMESTAMPTZ,
    valid_to            TIMESTAMPTZ,
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_id, contact_type, value_normalized, source_record_id)
);

CREATE INDEX IF NOT EXISTS ix_entity_contacts_lookup
    ON entity_contacts (contact_type, value_normalized)
    WHERE is_current = TRUE;

-- ---------------------------------------------------------------------------
-- ΑΝΑΠΤΥΞΗ project hierarchy, official aggregate payment observations, and
-- reviewable procurement-to-funding matches.
-- ---------------------------------------------------------------------------
ALTER TABLE funding_projects
    ADD COLUMN IF NOT EXISTS total_budget NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS completion NUMERIC(7,2),
    ADD COLUMN IF NOT EXISTS absorption NUMERIC(7,2),
    ADD COLUMN IF NOT EXISTS beneficiary_name_raw TEXT,
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS program_title TEXT,
    ADD COLUMN IF NOT EXISTS is_state_aid BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_major BOOLEAN,
    ADD COLUMN IF NOT EXISTS status_report TEXT,
    ADD COLUMN IF NOT EXISTS status_report_date TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS funds TEXT,
    ADD COLUMN IF NOT EXISTS spatial TEXT,
    ADD COLUMN IF NOT EXISTS thematic TEXT,
    ADD COLUMN IF NOT EXISTS map_kml TEXT,
    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_funding_projects_mis_period
    ON funding_projects (mis_ops_code, program_period);

CREATE TABLE IF NOT EXISTS funding_subprojects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    funding_project_id  UUID NOT NULL REFERENCES funding_projects(id) ON DELETE CASCADE,
    subproject_index    INTEGER NOT NULL,
    title               TEXT NOT NULL,
    implementors        JSONB,
    budget              NUMERIC(20,2),
    paid_amount         NUMERIC(20,2),
    completion          NUMERIC(7,2),
    start_date          DATE,
    end_date            DATE,
    subproject_type     TEXT,
    is_grant            BOOLEAN,
    estimated_status    JSONB NOT NULL DEFAULT '{}'::jsonb,
    actual_status       JSONB NOT NULL DEFAULT '{}'::jsonb,
    details             JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (funding_project_id, subproject_index)
);

CREATE TABLE IF NOT EXISTS funding_payment_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    funding_project_id  UUID NOT NULL REFERENCES funding_projects(id) ON DELETE CASCADE,
    funding_subproject_id UUID REFERENCES funding_subprojects(id) ON DELETE CASCADE,
    amount              NUMERIC(20,2) NOT NULL,
    payment_scope       TEXT NOT NULL,
    reference_date      DATE,
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (
        funding_project_id,
        funding_subproject_id,
        amount,
        payment_scope,
        observed_at
    )
);

CREATE INDEX IF NOT EXISTS ix_funding_payments_project
    ON funding_payment_snapshots (funding_project_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS funding_project_bodies (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    funding_project_id  UUID NOT NULL REFERENCES funding_projects(id) ON DELETE CASCADE,
    funding_subproject_id UUID REFERENCES funding_subprojects(id) ON DELETE CASCADE,
    body_category       TEXT,
    name                TEXT NOT NULL,
    representative      TEXT,
    address             TEXT,
    telephone           TEXT,
    email               TEXT,
    fax                 TEXT,
    entity_id           UUID REFERENCES entities(id),
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_funding_project_bodies_project
    ON funding_project_bodies (funding_project_id, body_category);

CREATE TABLE IF NOT EXISTS funding_geographic_allocations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    funding_project_id  UUID NOT NULL REFERENCES funding_projects(id) ON DELETE CASCADE,
    region              TEXT,
    prefecture          TEXT,
    municipality        TEXT,
    region_code         TEXT,
    prefecture_code     TEXT,
    municipality_code   TEXT,
    amount              NUMERIC(20,2),
    percentage          NUMERIC(7,3),
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE funding_links
    ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'AUTO_ACCEPTED',
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS review_notes TEXT;

-- ---------------------------------------------------------------------------
-- TED exact procedure/version linkage and eForms handling.
-- ---------------------------------------------------------------------------
ALTER TABLE ted_notice_details
    ADD COLUMN IF NOT EXISTS procedure_identifier TEXT,
    ADD COLUMN IF NOT EXISTS notice_version TEXT,
    ADD COLUMN IF NOT EXISTS sdk_customization_id TEXT,
    ADD COLUMN IF NOT EXISTS previous_notice_ids TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS change_notice_version_identifier TEXT,
    ADD COLUMN IF NOT EXISTS is_latest_version BOOLEAN NOT NULL DEFAULT TRUE;

DROP INDEX IF EXISTS uq_ted_notice_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ted_notice_version
    ON ted_notice_details (ted_notice_id, COALESCE(notice_version, ''));
CREATE INDEX IF NOT EXISTS ix_ted_procedure_identifier
    ON ted_notice_details (procedure_identifier)
    WHERE procedure_identifier IS NOT NULL;

-- ---------------------------------------------------------------------------
-- CKAN onboarding validation: schema fingerprints make live contract drift
-- visible and prevent a dataset from silently feeding the wrong adapter.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS external_dataset_validations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_dataset_id UUID NOT NULL REFERENCES external_datasets(id) ON DELETE CASCADE,
    adapter_name        TEXT NOT NULL,
    resource_url        TEXT NOT NULL,
    schema_fingerprint  TEXT NOT NULL,
    detected_format     TEXT,
    columns             TEXT[] NOT NULL DEFAULT '{}',
    sample              JSONB,
    status              TEXT NOT NULL,
    errors              JSONB NOT NULL DEFAULT '[]'::jsonb,
    validated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_dataset_validations_latest
    ON external_dataset_validations (external_dataset_id, validated_at DESC);

-- ---------------------------------------------------------------------------
-- Structured document intelligence and amendment/term comparison.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_compliance_fields (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id          UUID NOT NULL REFERENCES procurement_processes(id) ON DELETE CASCADE,
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number         INTEGER,
    category            TEXT NOT NULL,
    field_name          TEXT NOT NULL,
    value               JSONB NOT NULL,
    source_excerpt      TEXT,
    extraction_method   TEXT NOT NULL,
    parser_version      TEXT NOT NULL,
    confidence          NUMERIC(5,4) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (process_id, document_id, page_number, category, field_name, source_excerpt)
);

CREATE INDEX IF NOT EXISTS ix_compliance_fields_process
    ON document_compliance_fields (process_id, category);

CREATE TABLE IF NOT EXISTS document_comparisons (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id          UUID NOT NULL REFERENCES procurement_processes(id) ON DELETE CASCADE,
    base_document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    comparison_document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    comparison_type     TEXT NOT NULL,
    summary             TEXT NOT NULL,
    changes             JSONB NOT NULL,
    parser_version      TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (base_document_id, comparison_document_id, comparison_type, parser_version)
);

-- ---------------------------------------------------------------------------
-- Bid collaboration: comments/activity, reminders, reusable certificates,
-- and outbound CRM handoffs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bid_comments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    task_id             UUID REFERENCES bid_tasks(id) ON DELETE CASCADE,
    requirement_id      UUID REFERENCES bid_requirements(id) ON DELETE CASCADE,
    author_user_id      UUID NOT NULL REFERENCES users(id),
    body                TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bid_reminders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    task_id             UUID REFERENCES bid_tasks(id) ON DELETE CASCADE,
    requirement_id      UUID REFERENCES bid_requirements(id) ON DELETE CASCADE,
    assigned_user_id    UUID REFERENCES users(id),
    remind_at           TIMESTAMPTZ NOT NULL,
    channel             TEXT NOT NULL DEFAULT 'IN_APP',
    status              TEXT NOT NULL DEFAULT 'PENDING',
    sent_at             TIMESTAMPTZ,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_bid_reminders_due
    ON bid_reminders (status, remind_at)
    WHERE status = 'PENDING';

CREATE TABLE IF NOT EXISTS tenant_certificates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    title               TEXT NOT NULL,
    certificate_type    TEXT NOT NULL,
    issuer              TEXT,
    reference_number    TEXT,
    file_name           TEXT,
    storage_uri         TEXT,
    issued_at           DATE,
    expires_at          DATE,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bid_certificate_links (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    requirement_id      UUID REFERENCES bid_requirements(id) ON DELETE CASCADE,
    certificate_id      UUID NOT NULL REFERENCES tenant_certificates(id) ON DELETE CASCADE,
    linked_by           UUID NOT NULL REFERENCES users(id),
    linked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bid_workspace_id, requirement_id, certificate_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_certificate_link_scope
    ON bid_certificate_links (
        bid_workspace_id,
        COALESCE(requirement_id, '00000000-0000-0000-0000-000000000000'::uuid),
        certificate_id
    );

CREATE TABLE IF NOT EXISTS crm_handoffs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL,
    external_reference  TEXT,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    payload             JSONB NOT NULL,
    response            JSONB,
    error_message       TEXT,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    synced_at           TIMESTAMPTZ
);

-- Richer audit metadata for administrative access and data corrections.
ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS request_id TEXT,
    ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT 'SUCCESS';

-- Tenant isolation for every new collaboration table.
DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'bid_comments',
        'bid_reminders',
        'tenant_certificates',
        'bid_certificate_links',
        'crm_handoffs'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
            table_name
        );
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON bid_comments, bid_reminders, tenant_certificates, bid_certificate_links, crm_handoffs
    TO procintel_app;
