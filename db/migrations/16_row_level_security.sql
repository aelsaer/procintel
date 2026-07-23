-- 16_row_level_security.sql
-- Postgres row-level security for tenant-scoped tables — description.txt
-- §38 ("PostgreSQL row-level security" is named explicitly as the tenant
-- isolation mechanism) + §40.1.
--
-- Procurement data itself (procurement_acts, entities, ...) is explicitly
-- "shared public data" per §38 — RLS is NOT applied there, only to the
-- genuinely tenant-scoped tables that exist today: alert_rules,
-- alert_delivery_targets, alert_events, webhook_deliveries,
-- opportunity_scores. (saved_searches/notes/pipeline/tags/private_lists/
-- exports/api_keys/CRM mappings, also named tenant-specific in §38, don't
-- exist as tables yet — out of scope until something actually needs them.)
--
-- IMPORTANT: Postgres RLS policies never apply to the table owner or to
-- superuser roles, regardless of FORCE ROW LEVEL SECURITY. The role
-- created by `infra/docker/docker-compose.yml`'s POSTGRES_USER (used to
-- run these migrations) is the table owner — RLS is a no-op for it by
-- design. A separate, non-owner application role is required for RLS to
-- actually apply; see below and docs/runbooks/local-dev.md.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'procintel_app') THEN
        -- CHANGE_ME: this placeholder password must be rotated before any
        -- real deployment (ALTER ROLE procintel_app WITH PASSWORD '...').
        -- The dev-only default here mirrors docker-compose.yml's own
        -- POSTGRES_PASSWORD:-procintel convention.
        CREATE ROLE procintel_app NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN PASSWORD 'CHANGE_ME_procintel_app';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO procintel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO procintel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO procintel_app;

-- ---------------------------------------------------------------------------
-- alert_rules / alert_delivery_targets / webhook_deliveries / opportunity_scores
-- all carry tenant_id directly.
-- ---------------------------------------------------------------------------
ALTER TABLE alert_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_rules FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'alert_rules'
          AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON alert_rules
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$$;

ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'webhook_deliveries'
          AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON webhook_deliveries
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$$;

DO $$
BEGIN
    IF to_regclass('public.opportunity_scores') IS NOT NULL THEN
        ALTER TABLE opportunity_scores ENABLE ROW LEVEL SECURITY;
        ALTER TABLE opportunity_scores FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (
            SELECT 1
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = 'opportunity_scores'
              AND policyname = 'tenant_isolation'
        ) THEN
            CREATE POLICY tenant_isolation ON opportunity_scores
                USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
        END IF;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- alert_events / alert_delivery_targets have no tenant_id column of their
-- own — tenancy is via alert_rule_id -> alert_rules.tenant_id.
-- ---------------------------------------------------------------------------
ALTER TABLE alert_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_events FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'alert_events'
          AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON alert_events
            USING (
                alert_rule_id IN (
                    SELECT id FROM alert_rules WHERE tenant_id = current_setting('app.tenant_id', true)::uuid
                )
            );
    END IF;
END
$$;

ALTER TABLE alert_delivery_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_delivery_targets FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'alert_delivery_targets'
          AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON alert_delivery_targets
            USING (
                alert_rule_id IN (
                    SELECT id FROM alert_rules WHERE tenant_id = current_setting('app.tenant_id', true)::uuid
                )
            );
    END IF;
END
$$;

-- current_setting('app.tenant_id', true) returns NULL (not an error) when
-- unset — a connection that never calls set_config('app.tenant_id', ...)
-- (every ingestion connector/scheduler/mart-refresh job; none of them
-- touch these five tables per-tenant) sees zero rows via these policies,
-- not an error and not every tenant's rows. Those code paths currently
-- connect as the migration-owning superuser anyway, so RLS doesn't apply
-- to them regardless — this note is about `procintel_app` specifically.
