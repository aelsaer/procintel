-- Shared API request throttling across every ECS/API process. The table is
-- intentionally unlogged: rate-limit state is disposable after a DB restart.

CREATE UNLOGGED TABLE IF NOT EXISTS api_rate_limit_windows (
    identity TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL CHECK (request_count > 0),
    PRIMARY KEY (identity, window_start)
);

CREATE INDEX IF NOT EXISTS ix_api_rate_limit_windows_cleanup
    ON api_rate_limit_windows (window_start);
