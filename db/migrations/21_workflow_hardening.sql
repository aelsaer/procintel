-- 21_workflow_hardening.sql
-- Idempotent digest periods and operational indexes for product workers.

ALTER TABLE alert_digest_runs ADD COLUMN IF NOT EXISTS period_key TEXT;

UPDATE alert_digest_runs
SET period_key = COALESCE(
    error ->> 'period_key',
    CASE
        WHEN schedule = 'WEEKLY_DIGEST'
            THEN to_char(period_ended_at AT TIME ZONE 'UTC', 'IYYY-"W"IW')
        ELSE to_char(period_ended_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')
    END
)
WHERE period_key IS NULL;

ALTER TABLE alert_digest_runs ALTER COLUMN period_key SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_digest_period
    ON alert_digest_runs (alert_rule_id, schedule, period_key)
    WHERE alert_rule_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_export_jobs_pending
    ON export_jobs (created_at)
    WHERE status IN ('PENDING', 'FAILED');

CREATE INDEX IF NOT EXISTS ix_entity_match_candidates_review
    ON entity_match_candidates (score DESC, created_at)
    WHERE status = 'PENDING_REVIEW';
