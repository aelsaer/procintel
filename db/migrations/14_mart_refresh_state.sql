-- 14_mart_refresh_state.sql
-- Watermark table for scheduled analytics-mart refresh
-- (services/analytics/refresh.py), same role for `db/marts/*.sql`'s
-- materialized views that source_cursors already plays for connector
-- ingestion: "when did this last succeed, what broke last time".

CREATE TABLE mart_refresh_state (
    mart_name                  TEXT PRIMARY KEY,
    last_refresh_started_at    TIMESTAMPTZ,
    last_refresh_finished_at   TIMESTAMPTZ,
    last_error                 JSONB
);
