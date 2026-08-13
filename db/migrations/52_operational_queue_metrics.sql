-- Expose aggregate queue health to the application role without granting it
-- cross-tenant access to the underlying RLS-protected job payloads.

CREATE OR REPLACE FUNCTION procintel_operational_queue_metrics()
RETURNS TABLE (
    fetch_queue BIGINT,
    export_queue BIGINT,
    scoring_queue BIGINT,
    webhook_queue BIGINT,
    oldest_durable_job_age_seconds DOUBLE PRECISION
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH durable_jobs AS (
        SELECT requested_at AS enqueued_at, 'fetch'::TEXT AS queue_name
          FROM fetch_requests
         WHERE status = 'QUEUED'
           AND (next_retry_at IS NULL OR next_retry_at <= now())
        UNION ALL
        SELECT created_at, 'export'
          FROM export_jobs
         WHERE status = 'PENDING'
        UNION ALL
        SELECT requested_at, 'scoring'
          FROM opportunity_score_jobs
         WHERE status IN ('QUEUED', 'FAILED')
        UNION ALL
        SELECT created_at, 'webhook'
          FROM webhook_deliveries
         WHERE (status = 'PENDING' AND (next_retry_at IS NULL OR next_retry_at <= now()))
            OR (
                status = 'DELIVERING'
                AND (
                    last_attempt_at IS NULL
                    OR last_attempt_at < now() - interval '15 minutes'
                )
            )
    )
    SELECT
        COUNT(*) FILTER (WHERE queue_name = 'fetch')::BIGINT,
        COUNT(*) FILTER (WHERE queue_name = 'export')::BIGINT,
        COUNT(*) FILTER (WHERE queue_name = 'scoring')::BIGINT,
        COUNT(*) FILTER (WHERE queue_name = 'webhook')::BIGINT,
        EXTRACT(EPOCH FROM (clock_timestamp() - COALESCE(MIN(enqueued_at), clock_timestamp())))::DOUBLE PRECISION
      FROM durable_jobs;
$$;

REVOKE ALL ON FUNCTION procintel_operational_queue_metrics() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION procintel_operational_queue_metrics() TO procintel_app;
