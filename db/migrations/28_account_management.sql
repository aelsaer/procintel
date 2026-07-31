-- Team invitations and production-usable API key lifecycle.

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'API key';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS tenant_invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    email           TEXT NOT NULL,
    role            TEXT NOT NULL,
    token_hash      TEXT NOT NULL UNIQUE,
    invited_by      UUID NOT NULL REFERENCES users(id),
    expires_at      TIMESTAMPTZ NOT NULL,
    accepted_at     TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (role IN ('OWNER', 'ADMIN', 'ANALYST', 'SALES', 'BID_MANAGER', 'VIEWER'))
);

CREATE INDEX IF NOT EXISTS ix_tenant_invitations_active
    ON tenant_invitations (tenant_id, lower(email), expires_at)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;

DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['api_keys', 'tenant_invitations', 'tenant_memberships']
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
    ON api_keys, tenant_invitations, tenant_memberships
    TO procintel_app;
