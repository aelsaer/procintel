-- 17_on_demand_fetch_requests.sql
-- Persistent status for user-triggered exact-identifier fetches. Search must
-- return immediately; provider calls happen asynchronously and are rate-limited
-- by the existing source clients.

CREATE TABLE fetch_requests (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identifier_raw           TEXT NOT NULL,
    identifier_normalized      TEXT NOT NULL,
    identifier_scheme            TEXT NOT NULL, -- ADAM | ADA
    source_system                  TEXT NOT NULL, -- KHMDHS | DIAVGEIA
    status                           TEXT NOT NULL DEFAULT 'QUEUED',
    message                            TEXT,
    result_act_id                       UUID REFERENCES procurement_acts(id),
    result_process_id                     UUID REFERENCES procurement_processes(id),
    requested_at                           TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at                               TIMESTAMPTZ,
    finished_at                                TIMESTAMPTZ,
    last_attempt_at                              TIMESTAMPTZ,
    attempt_count                                  INTEGER NOT NULL DEFAULT 0,
    next_retry_at                                    TIMESTAMPTZ,
    request_metadata                                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at                                          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (identifier_scheme IN ('ADAM', 'ADA')),
    CHECK (source_system IN ('KHMDHS', 'DIAVGEIA')),
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'NOT_FOUND', 'WAITING_FOR_CONFIG', 'FAILED')),
    UNIQUE (identifier_scheme, identifier_normalized)
);

CREATE INDEX ix_fetch_requests_status
    ON fetch_requests (status, requested_at DESC);

CREATE INDEX ix_fetch_requests_identifier
    ON fetch_requests (identifier_scheme, identifier_normalized);
