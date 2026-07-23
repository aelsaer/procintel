-- 08_multitenancy_and_workspace.sql
-- Multi-tenancy: shared public canonical data + isolated tenant workspace
-- (users, saved searches, alerts, notes, opportunity pipeline, tags, API
-- keys, audit log). Spec refs: description.txt §38-40.

CREATE TABLE tenants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  TEXT NOT NULL,
    plan                    TEXT NOT NULL DEFAULT 'STARTER',  -- STARTER | PROFESSIONAL | ENTERPRISE | DATA_API (§42)
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- users are global identities; tenant_memberships grants tenant-scoped roles.
-- is_internal_reviewer marks the operational role from §39 that reviews data
-- quality/entity matches without automatic access to tenant private notes.
CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                  TEXT UNIQUE NOT NULL,
    display_name              TEXT,
    is_internal_reviewer         BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_enabled                    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tenant_memberships (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    user_id                 UUID NOT NULL REFERENCES users(id),
    role                       TEXT NOT NULL,
        -- OWNER | ADMIN | ANALYST | SALES | BID_MANAGER | VIEWER | API_CLIENT (§39)
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id)
);

-- ---------------------------------------------------------------------------
-- api_keys (§40.2): only the hash is stored.
-- ---------------------------------------------------------------------------
CREATE TABLE api_keys (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    key_prefix              TEXT NOT NULL,
    key_hash                  TEXT NOT NULL,
    scopes                      TEXT[] NOT NULL DEFAULT '{}',
    created_by                    UUID REFERENCES users(id),
    expires_at                      TIMESTAMPTZ,
    last_used_at                      TIMESTAMPTZ,
    created_at                          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_api_keys_hash ON api_keys (key_hash);
CREATE INDEX ix_api_keys_tenant ON api_keys (tenant_id);

-- ---------------------------------------------------------------------------
-- audit_log (§40.3): login, export, api key creation, alert changes, entity
-- merge/split, manual link confirmation, data correction, admin access.
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID REFERENCES tenants(id),     -- NULL for internal/admin-only actions
    actor_user_id           UUID REFERENCES users(id),
    action                     TEXT NOT NULL,
    object_type                 TEXT,
    object_id                     UUID,
    details                          JSONB,
    ip_address                         INET,
    created_at                           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_audit_log_tenant ON audit_log (tenant_id, created_at DESC);
CREATE INDEX ix_audit_log_actor ON audit_log (actor_user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Tenant workspace: saved searches, opportunity pipeline, notes, tags.
-- ---------------------------------------------------------------------------
CREATE TABLE saved_searches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    user_id                 UUID NOT NULL REFERENCES users(id),
    name                       TEXT NOT NULL,
    query                        JSONB NOT NULL,             -- serialized filters, §30.2
    created_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE opportunity_pipeline_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    user_id                 UUID NOT NULL REFERENCES users(id),
    process_id                 UUID NOT NULL REFERENCES procurement_processes(id),
    stage                        TEXT NOT NULL DEFAULT 'WATCHING',  -- WATCHING | QUALIFYING | BID_NO_BID | BIDDING | WON | LOST | DROPPED
    added_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, process_id, user_id)
);

CREATE TABLE notes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    user_id                 UUID NOT NULL REFERENCES users(id),
    object_type                TEXT NOT NULL,               -- procurement_processes | entities | ...
    object_id                    UUID NOT NULL,
    body                            TEXT NOT NULL,
    created_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tags (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    name                    TEXT NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE tag_links (
    tag_id              UUID NOT NULL REFERENCES tags(id),
    object_type            TEXT NOT NULL,
    object_id                UUID NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tag_id, object_type, object_id)
);

CREATE INDEX ix_notes_object ON notes (tenant_id, object_type, object_id);
CREATE INDEX ix_pipeline_tenant ON opportunity_pipeline_items (tenant_id, stage);

-- ---------------------------------------------------------------------------
-- Row-level security sketch (§38): canonical tables (entities, processes,
-- acts, ...) stay globally readable; tenant-scoped tables enforce isolation
-- via RLS keyed on the session's tenant_id. Policies themselves belong to a
-- Στάδιο-1 hardening migration once the auth/session layer exists; shown here
-- for one representative table so the pattern is on record.
-- ---------------------------------------------------------------------------
-- ALTER TABLE saved_searches ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY tenant_isolation ON saved_searches
--     USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
