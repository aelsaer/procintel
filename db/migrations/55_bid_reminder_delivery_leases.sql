-- Durable bid-reminder delivery with recoverable leases and retries.

ALTER TABLE bid_reminders
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE bid_reminders
    ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;

ALTER TABLE bid_reminders
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

ALTER TABLE bid_reminders
    ADD COLUMN IF NOT EXISTS last_error JSONB;

CREATE INDEX IF NOT EXISTS ix_bid_reminders_delivery_queue
    ON bid_reminders (status, next_retry_at, last_attempt_at, remind_at)
    WHERE status IN ('PENDING', 'DELIVERING');

CREATE OR REPLACE FUNCTION procintel_reminder_queue_metrics()
RETURNS TABLE (
    reminder_queue BIGINT,
    oldest_reminder_job_age_seconds DOUBLE PRECISION
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT
        COUNT(*)::BIGINT,
        EXTRACT(EPOCH FROM (
            clock_timestamp() - COALESCE(MIN(remind_at), clock_timestamp())
        ))::DOUBLE PRECISION
      FROM bid_reminders
     WHERE (
              status = 'PENDING'
              AND remind_at <= now()
              AND (next_retry_at IS NULL OR next_retry_at <= now())
           )
        OR (
              status = 'DELIVERING'
              AND (
                  last_attempt_at IS NULL
                  OR last_attempt_at < now() - interval '15 minutes'
              )
           );
$$;

REVOKE ALL ON FUNCTION procintel_reminder_queue_metrics() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION procintel_reminder_queue_metrics() TO procintel_app;
