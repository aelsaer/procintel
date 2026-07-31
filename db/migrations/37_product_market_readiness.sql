-- Product market-readiness workflows: guided onboarding, completeness SLAs,
-- decision makers, framework intelligence, proposal production, commercial
-- subscriptions, practical document tools, and European benchmarking.

CREATE TABLE IF NOT EXISTS onboarding_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES tenants(id),
    user_id                 UUID NOT NULL REFERENCES users(id),
    business_profile_id     UUID REFERENCES business_profiles(id),
    status                  TEXT NOT NULL DEFAULT 'DRAFT',
    current_step            TEXT NOT NULL DEFAULT 'COMPANY',
    company_description     TEXT NOT NULL DEFAULT '',
    cpv_suggestions         JSONB NOT NULL DEFAULT '[]'::jsonb,
    selected_cpv_codes      TEXT[] NOT NULL DEFAULT '{}',
    selected_keywords       TEXT[] NOT NULL DEFAULT '{}',
    selected_nuts_codes     TEXT[] NOT NULL DEFAULT '{}',
    initial_opportunity_ids UUID[] NOT NULL DEFAULT '{}',
    quality_score           NUMERIC(5,2),
    quality_findings        JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN (
        'DRAFT', 'AWAITING_CONFIRMATION', 'SCORING', 'IN_REVIEW',
        'COMPLETED', 'NEEDS_ATTENTION'
    ))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_onboarding_active_user
    ON onboarding_sessions (tenant_id, user_id)
    WHERE status <> 'COMPLETED';

CREATE TABLE IF NOT EXISTS profile_review_requests (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    onboarding_session_id UUID REFERENCES onboarding_sessions(id) ON DELETE CASCADE,
    business_profile_id UUID NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
    requested_by        UUID NOT NULL REFERENCES users(id),
    assigned_to         UUID REFERENCES users(id),
    status              TEXT NOT NULL DEFAULT 'OPEN',
    priority            TEXT NOT NULL DEFAULT 'NORMAL',
    request_notes       TEXT,
    reviewer_notes      TEXT,
    recommended_changes JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at         TIMESTAMPTZ,
    CHECK (status IN ('OPEN', 'ASSIGNED', 'APPROVED', 'CHANGES_REQUESTED', 'CLOSED')),
    CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT'))
);

CREATE INDEX IF NOT EXISTS ix_profile_review_queue
    ON profile_review_requests (status, priority, requested_at);

CREATE TABLE IF NOT EXISTS source_completeness_snapshots (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system           TEXT NOT NULL,
    snapshot_date           DATE NOT NULL,
    window_started_at       TIMESTAMPTZ NOT NULL,
    window_ended_at         TIMESTAMPTZ NOT NULL,
    expected_records        INTEGER,
    observed_records        INTEGER NOT NULL DEFAULT 0,
    canonical_records       INTEGER NOT NULL DEFAULT 0,
    records_with_documents  INTEGER NOT NULL DEFAULT 0,
    records_with_parties    INTEGER NOT NULL DEFAULT 0,
    records_with_locations  INTEGER NOT NULL DEFAULT 0,
    failed_records          INTEGER NOT NULL DEFAULT 0,
    pending_enrichments     INTEGER NOT NULL DEFAULT 0,
    freshness_seconds       BIGINT,
    completeness_score      NUMERIC(6,3) NOT NULL,
    status                  TEXT NOT NULL,
    dimensions              JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence                JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, snapshot_date, window_started_at, window_ended_at),
    CHECK (status IN ('HEALTHY', 'DEGRADED', 'PARTIAL', 'STALE', 'UNAVAILABLE'))
);

CREATE INDEX IF NOT EXISTS ix_source_completeness_latest
    ON source_completeness_snapshots (source_system, computed_at DESC);

CREATE TABLE IF NOT EXISTS source_service_levels (
    source_system           TEXT PRIMARY KEY,
    freshness_target_seconds BIGINT NOT NULL,
    minimum_completeness    NUMERIC(6,3) NOT NULL DEFAULT 95,
    minimum_document_rate   NUMERIC(6,3),
    minimum_party_rate      NUMERIC(6,3),
    description             TEXT NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO source_service_levels (
    source_system, freshness_target_seconds, minimum_completeness,
    minimum_document_rate, minimum_party_rate, description
) VALUES
    ('KHMDHS', 108000, 98, 90, 95, 'Daily Greek procurement publication coverage'),
    ('DIAVGEIA', 108000, 95, 85, 80, 'Decisions, signers, organizations and documents'),
    ('GEMI', 604800, 90, NULL, 90, 'Company identity and registry enrichment'),
    ('ANAPTYXI', 604800, 90, NULL, 75, 'Funding projects, subprojects and payments'),
    ('MEF', 172800, 90, 75, 85, 'Public expense linkage'),
    ('TED', 172800, 95, 85, 90, 'Greek and European notice coverage')
ON CONFLICT (source_system) DO NOTHING;

CREATE TABLE IF NOT EXISTS decision_makers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_entity_id     UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    full_name           TEXT NOT NULL,
    job_title           TEXT,
    department          TEXT,
    decision_role       TEXT NOT NULL DEFAULT 'STAKEHOLDER',
    email               TEXT,
    phone               TEXT,
    profile_url         TEXT,
    source_system       TEXT NOT NULL,
    source_url          TEXT,
    source_identifier   TEXT,
    source_record_id    UUID REFERENCES source_records(id),
    legal_basis         TEXT NOT NULL DEFAULT 'PUBLIC_OFFICIAL_RECORD',
    confidence          NUMERIC(5,4) NOT NULL DEFAULT 0.5,
    active_from         TIMESTAMPTZ,
    active_until        TIMESTAMPTZ,
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    evidence            JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (buyer_entity_id, full_name, decision_role, source_system, source_identifier)
);

CREATE INDEX IF NOT EXISTS ix_decision_makers_buyer
    ON decision_makers (buyer_entity_id, is_current, confidence DESC);

CREATE TABLE IF NOT EXISTS decision_maker_watches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    user_id             UUID NOT NULL REFERENCES users(id),
    decision_maker_id   UUID NOT NULL REFERENCES decision_makers(id) ON DELETE CASCADE,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, decision_maker_id)
);

CREATE TABLE IF NOT EXISTS framework_supplier_memberships (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    framework_act_id    UUID NOT NULL REFERENCES procurement_acts(id) ON DELETE CASCADE,
    supplier_entity_id  UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    lot_identifier      TEXT,
    membership_status   TEXT NOT NULL DEFAULT 'ACTIVE',
    awarded_value       NUMERIC(20,2),
    valid_from          DATE,
    valid_until         DATE,
    source_record_id    UUID REFERENCES source_records(id),
    evidence            JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_framework_memberships_supplier
    ON framework_supplier_memberships (supplier_entity_id, membership_status, valid_until);

CREATE UNIQUE INDEX IF NOT EXISTS uq_framework_membership_lot
    ON framework_supplier_memberships (
        framework_act_id, supplier_entity_id, COALESCE(lot_identifier, '')
    );

CREATE TABLE IF NOT EXISTS framework_watches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    user_id             UUID NOT NULL REFERENCES users(id),
    framework_act_id    UUID NOT NULL REFERENCES procurement_acts(id) ON DELETE CASCADE,
    notify_before_days  INTEGER NOT NULL DEFAULT 90,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, framework_act_id)
);

CREATE TABLE IF NOT EXISTS tenant_bid_content (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    title               TEXT NOT NULL,
    content_type        TEXT NOT NULL,
    body                TEXT NOT NULL,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    source_file_name    TEXT,
    source_uri          TEXT,
    approved            BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by         UUID REFERENCES users(id),
    approved_at         TIMESTAMPTZ,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tenant_bid_content_search
    ON tenant_bid_content (tenant_id, content_type, approved);

CREATE TABLE IF NOT EXISTS proposal_sections (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    requirement_id      UUID REFERENCES bid_requirements(id) ON DELETE SET NULL,
    section_key         TEXT NOT NULL,
    title               TEXT NOT NULL,
    display_order       INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'DRAFT',
    current_version     INTEGER NOT NULL DEFAULT 1,
    body                TEXT NOT NULL DEFAULT '',
    citations           JSONB NOT NULL DEFAULT '[]'::jsonb,
    generation_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    assigned_user_id    UUID REFERENCES users(id),
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bid_workspace_id, section_key),
    CHECK (status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'NEEDS_CHANGES'))
);

CREATE TABLE IF NOT EXISTS proposal_section_versions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    proposal_section_id UUID NOT NULL REFERENCES proposal_sections(id) ON DELETE CASCADE,
    version_number      INTEGER NOT NULL,
    body                TEXT NOT NULL,
    citations           JSONB NOT NULL DEFAULT '[]'::jsonb,
    change_summary      TEXT,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (proposal_section_id, version_number)
);

CREATE TABLE IF NOT EXISTS proposal_exports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    bid_workspace_id    UUID NOT NULL REFERENCES bid_workspaces(id) ON DELETE CASCADE,
    format              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    file_name           TEXT,
    storage_path        TEXT,
    manifest            JSONB NOT NULL DEFAULT '{}'::jsonb,
    error               JSONB,
    created_by          UUID NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    CHECK (format IN ('DOCX', 'PDF', 'ZIP'))
);

CREATE TABLE IF NOT EXISTS saas_plans (
    code                TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    monthly_price_cents INTEGER,
    annual_price_cents  INTEGER,
    currency            TEXT NOT NULL DEFAULT 'EUR',
    trial_days          INTEGER NOT NULL DEFAULT 14,
    entitlements        JSONB NOT NULL,
    is_public           BOOLEAN NOT NULL DEFAULT TRUE,
    display_order       INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO saas_plans (
    code, name, description, monthly_price_cents, annual_price_cents,
    trial_days, entitlements, display_order
) VALUES
    ('STARTER', 'Starter', 'Discovery and daily opportunity monitoring', 9900, 99000, 14,
     '{"users":2,"cpv_codes":10,"ai_reports_month":20,"proposal_drafts_month":5,"exports_month":20,"api":false,"managed_review":false}'::jsonb, 10),
    ('PROFESSIONAL', 'Professional', 'Intelligence, collaboration and bid production', 34900, 349000, 14,
     '{"users":10,"cpv_codes":50,"ai_reports_month":200,"proposal_drafts_month":50,"exports_month":200,"api":true,"managed_review":true}'::jsonb, 20),
    ('ENTERPRISE', 'Enterprise', 'Multi-team intelligence with SLA and integrations', NULL, NULL, 30,
     '{"users":-1,"cpv_codes":-1,"ai_reports_month":-1,"proposal_drafts_month":-1,"exports_month":-1,"api":true,"managed_review":true,"sla":true,"sso":true}'::jsonb, 30)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    monthly_price_cents = EXCLUDED.monthly_price_cents,
    annual_price_cents = EXCLUDED.annual_price_cents,
    trial_days = EXCLUDED.trial_days,
    entitlements = EXCLUDED.entitlements,
    display_order = EXCLUDED.display_order,
    updated_at = now();

CREATE TABLE IF NOT EXISTS tenant_subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL UNIQUE REFERENCES tenants(id),
    plan_code               TEXT NOT NULL REFERENCES saas_plans(code),
    status                  TEXT NOT NULL DEFAULT 'TRIALING',
    billing_provider        TEXT NOT NULL DEFAULT 'MANUAL',
    provider_customer_id    TEXT,
    provider_subscription_id TEXT,
    trial_started_at        TIMESTAMPTZ,
    trial_ends_at           TIMESTAMPTZ,
    current_period_start    TIMESTAMPTZ,
    current_period_end      TIMESTAMPTZ,
    cancel_at_period_end    BOOLEAN NOT NULL DEFAULT FALSE,
    entitlements_override   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('TRIALING', 'ACTIVE', 'PAST_DUE', 'CANCELED', 'PAUSED'))
);

CREATE TABLE IF NOT EXISTS entitlement_usage (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    metric_code         TEXT NOT NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    usage_count         BIGINT NOT NULL DEFAULT 0,
    last_incremented_at TIMESTAMPTZ,
    UNIQUE (tenant_id, metric_code, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    created_by          UUID NOT NULL REFERENCES users(id),
    assigned_to         UUID REFERENCES users(id),
    subject             TEXT NOT NULL,
    description         TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT 'PRODUCT',
    priority            TEXT NOT NULL DEFAULT 'NORMAL',
    status              TEXT NOT NULL DEFAULT 'OPEN',
    response_due_at     TIMESTAMPTZ,
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('OPEN', 'IN_PROGRESS', 'WAITING_CUSTOMER', 'RESOLVED', 'CLOSED'))
);

CREATE TABLE IF NOT EXISTS account_success_tasks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    owner_user_id       UUID REFERENCES users(id),
    title               TEXT NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'OPEN',
    due_at              TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service_incidents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    affected_components TEXT[] NOT NULL DEFAULT '{}',
    severity            TEXT NOT NULL,
    status              TEXT NOT NULL,
    public_message      TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    resolved_at         TIMESTAMPTZ,
    updates             JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sector_profile_templates (
    code                    TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    description             TEXT NOT NULL,
    cpv_prefixes            TEXT[] NOT NULL,
    keywords                TEXT[] NOT NULL DEFAULT '{}',
    excluded_keywords       TEXT[] NOT NULL DEFAULT '{}',
    recommended_alerts      JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    display_order           INTEGER NOT NULL DEFAULT 0
);

INSERT INTO sector_profile_templates (
    code, name, description, cpv_prefixes, keywords, excluded_keywords,
    recommended_alerts, display_order
) VALUES
    ('ICT', 'Πληροφορική και ψηφιακές υπηρεσίες', 'Λογισμικό, υποδομές, κυβερνοασφάλεια, GIS και συμβουλευτική ΙΤ',
     ARRAY['72','48','302','324'], ARRAY['λογισμικό','GIS','κυβερνοασφάλεια','ψηφιακός μετασχηματισμός'], ARRAY[]::TEXT[],
     '[{"type":"NEW_OPPORTUNITY","schedule":"DAILY_DIGEST"},{"type":"CONTRACT_EXPIRING","schedule":"WEEKLY_DIGEST"}]'::jsonb, 10),
    ('ENVIRONMENT', 'Περιβάλλον και πράσινο', 'Αποψιλώσεις, συντήρηση πρασίνου, καθαρισμοί και περιβαλλοντικές υπηρεσίες',
     ARRAY['773','772','907'], ARRAY['αποψίλωση','καθαρισμός βλάστησης','συντήρηση πρασίνου'], ARRAY[]::TEXT[],
     '[{"type":"NEW_OPPORTUNITY","schedule":"DAILY_DIGEST"}]'::jsonb, 20),
    ('CONSTRUCTION', 'Κατασκευές και τεχνικά έργα', 'Κτιριακά, οδικά, υδραυλικά και τεχνικές μελέτες',
     ARRAY['45','71'], ARRAY['κατασκευή','τεχνικό έργο','μελέτη'], ARRAY[]::TEXT[],
     '[{"type":"NEW_OPPORTUNITY","schedule":"DAILY_DIGEST"},{"type":"PRE_TENDER_SIGNAL","schedule":"WEEKLY_DIGEST"}]'::jsonb, 30),
    ('HEALTH', 'Υγεία και ιατροτεχνολογικά', 'Ιατρικός εξοπλισμός, αναλώσιμα, φάρμακα και υπηρεσίες υγείας',
     ARRAY['33','851'], ARRAY['ιατροτεχνολογικό','νοσοκομείο','ιατρικά αναλώσιμα'], ARRAY[]::TEXT[],
     '[{"type":"NEW_OPPORTUNITY","schedule":"DAILY_DIGEST"}]'::jsonb, 40),
    ('CONSULTING', 'Συμβουλευτικές υπηρεσίες', 'Διοικητική, οικονομική, επιχειρησιακή και τεχνική υποστήριξη',
     ARRAY['79'], ARRAY['σύμβουλος','τεχνική υποστήριξη','μελέτη'], ARRAY['νομικές υπηρεσίες'],
     '[{"type":"NEW_OPPORTUNITY","schedule":"DAILY_DIGEST"}]'::jsonb, 50)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    cpv_prefixes = EXCLUDED.cpv_prefixes,
    keywords = EXCLUDED.keywords,
    excluded_keywords = EXCLUDED.excluded_keywords,
    recommended_alerts = EXCLUDED.recommended_alerts,
    display_order = EXCLUDED.display_order;

CREATE TABLE IF NOT EXISTS document_phrase_monitors (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    user_id             UUID NOT NULL REFERENCES users(id),
    name                TEXT NOT NULL,
    phrases             TEXT[] NOT NULL,
    match_mode          TEXT NOT NULL DEFAULT 'ANY',
    cpv_prefixes        TEXT[] NOT NULL DEFAULT '{}',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (match_mode IN ('ANY', 'ALL', 'EXACT'))
);

CREATE TABLE IF NOT EXISTS document_phrase_matches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    monitor_id          UUID NOT NULL REFERENCES document_phrase_monitors(id) ON DELETE CASCADE,
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    process_id          UUID REFERENCES procurement_processes(id) ON DELETE CASCADE,
    matched_phrases     TEXT[] NOT NULL,
    page_numbers        INTEGER[] NOT NULL DEFAULT '{}',
    excerpts            JSONB NOT NULL DEFAULT '[]'::jsonb,
    matched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (monitor_id, document_id)
);

CREATE TABLE IF NOT EXISTS document_transformation_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    user_id             UUID NOT NULL REFERENCES users(id),
    transformation_type TEXT NOT NULL,
    document_ids        UUID[] NOT NULL,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    file_name           TEXT,
    storage_path        TEXT,
    error               JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    CHECK (transformation_type IN ('BULK_ZIP', 'PDF_TO_DOCX', 'EVIDENCE_BUNDLE'))
);

CREATE TABLE IF NOT EXISTS eu_benchmark_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date       DATE NOT NULL,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    cpv_prefix          TEXT NOT NULL,
    country_code        TEXT NOT NULL,
    notice_count        INTEGER NOT NULL,
    award_count         INTEGER NOT NULL,
    total_value         NUMERIC(24,2) NOT NULL DEFAULT 0,
    median_value        NUMERIC(24,2),
    average_bidders     NUMERIC(10,3),
    sme_award_rate      NUMERIC(7,4),
    single_bid_rate     NUMERIC(7,4),
    dimensions          JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_date, date_from, date_to, cpv_prefix, country_code)
);

CREATE TABLE IF NOT EXISTS tenant_cross_border_matches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id),
    process_id          UUID NOT NULL REFERENCES procurement_processes(id) ON DELETE CASCADE,
    profile_version     INTEGER NOT NULL,
    country_code        TEXT NOT NULL,
    match_score         NUMERIC(5,2) NOT NULL,
    reasons             JSONB NOT NULL DEFAULT '[]'::jsonb,
    barriers            JSONB NOT NULL DEFAULT '[]'::jsonb,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, process_id, profile_version)
);

CREATE INDEX IF NOT EXISTS ix_cross_border_matches_tenant
    ON tenant_cross_border_matches (tenant_id, profile_version, match_score DESC);

DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'onboarding_sessions', 'profile_review_requests',
        'decision_maker_watches', 'framework_watches', 'tenant_bid_content',
        'proposal_sections', 'proposal_section_versions', 'proposal_exports',
        'tenant_subscriptions', 'entitlement_usage', 'support_tickets',
        'account_success_tasks', 'document_phrase_monitors',
        'document_phrase_matches', 'document_transformation_jobs',
        'tenant_cross_border_matches'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
            table_name
        );
    END LOOP;
END
$$;

GRANT SELECT ON
    source_completeness_snapshots, source_service_levels, decision_makers,
    framework_supplier_memberships, saas_plans, service_incidents,
    sector_profile_templates, eu_benchmark_snapshots
TO procintel_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    onboarding_sessions, profile_review_requests, decision_maker_watches,
    framework_watches, tenant_bid_content, proposal_sections,
    proposal_section_versions, proposal_exports, tenant_subscriptions,
    entitlement_usage, support_tickets, account_success_tasks,
    document_phrase_monitors, document_phrase_matches,
    document_transformation_jobs, tenant_cross_border_matches
TO procintel_app;
