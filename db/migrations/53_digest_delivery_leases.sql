-- Make digest delivery recoverable after worker interruption and retryable
-- after transient channel failures.

ALTER TABLE alert_digest_runs
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE alert_digest_runs
    ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;

ALTER TABLE alert_digest_runs
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

UPDATE alert_digest_runs
   SET attempt_count = GREATEST(attempt_count, 1),
       last_attempt_at = COALESCE(last_attempt_at, created_at)
 WHERE status = 'RUNNING';

UPDATE alert_digest_runs
   SET next_retry_at = COALESCE(next_retry_at, now())
 WHERE status = 'FAILED';

CREATE INDEX IF NOT EXISTS ix_alert_digest_runs_retry
    ON alert_digest_runs (status, next_retry_at, last_attempt_at)
    WHERE status IN ('PENDING', 'RUNNING', 'FAILED');

CREATE OR REPLACE FUNCTION procintel_digest_queue_metrics()
RETURNS TABLE (
    digest_queue BIGINT,
    oldest_digest_job_age_seconds DOUBLE PRECISION
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT
        COUNT(*)::BIGINT,
        EXTRACT(EPOCH FROM (
            clock_timestamp() - COALESCE(MIN(created_at), clock_timestamp())
        ))::DOUBLE PRECISION
      FROM alert_digest_runs
     WHERE status = 'PENDING'
        OR (status = 'FAILED' AND (next_retry_at IS NULL OR next_retry_at <= now()))
        OR (
            status = 'RUNNING'
            AND (
                last_attempt_at IS NULL
                OR last_attempt_at < now() - interval '15 minutes'
            )
        );
$$;

REVOKE ALL ON FUNCTION procintel_digest_queue_metrics() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION procintel_digest_queue_metrics() TO procintel_app;
