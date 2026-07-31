-- Keep tenant-relative Radar state attached to the business profile version
-- that produced it. Profile changes immediately hide stale scores and
-- relevance feedback while preserving the historical rows for auditability.

-- opportunity_scores is created by db/marts/analytics_marts.sql. Existing
-- installations already have it when this migration runs; a fresh database
-- creates it after migrations, with profile_version in the mart DDL itself.
DO $$
BEGIN
    IF to_regclass('public.opportunity_scores') IS NOT NULL THEN
        EXECUTE '
            ALTER TABLE opportunity_scores
            ADD COLUMN IF NOT EXISTS profile_version INTEGER NOT NULL DEFAULT 1
        ';
        EXECUTE '
            UPDATE opportunity_scores score
            SET profile_version = profile.classification_version
            FROM business_profiles profile
            WHERE profile.tenant_id = score.tenant_id
        ';
        EXECUTE '
            CREATE INDEX IF NOT EXISTS ix_opportunity_scores_tenant_profile
            ON opportunity_scores (tenant_id, profile_version, total_score DESC)
        ';
    END IF;
END
$$;

ALTER TABLE opportunity_relevance_feedback
    ADD COLUMN IF NOT EXISTS profile_version INTEGER NOT NULL DEFAULT 1;

-- Legacy feedback cannot be attributed safely because the profile version was
-- not stored when it was created. Mark it as historical instead of letting an
-- old manual decision override a newly classified business profile.
UPDATE opportunity_relevance_feedback
SET profile_version = 0;

CREATE INDEX IF NOT EXISTS ix_relevance_feedback_tenant_profile
    ON opportunity_relevance_feedback (tenant_id, profile_version, updated_at DESC);
