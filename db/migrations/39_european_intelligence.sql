-- TED European cohorts and cross-border opportunities reference the source act.
-- A foreign TED notice commonly has no Greek procurement_process linkage.

ALTER TABLE tenant_cross_border_matches
    ADD COLUMN IF NOT EXISTS act_id UUID REFERENCES procurement_acts(id) ON DELETE CASCADE;

ALTER TABLE tenant_cross_border_matches
    ALTER COLUMN process_id DROP NOT NULL;

UPDATE tenant_cross_border_matches match
SET act_id = (
    SELECT act.id
    FROM procurement_acts act
    WHERE act.process_id = match.process_id
      AND act.act_type = 'TED_NOTICE'
    ORDER BY act.publication_date DESC NULLS LAST, act.updated_at DESC
    LIMIT 1
)
WHERE match.act_id IS NULL;

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT con.conname
    INTO constraint_name
    FROM pg_constraint con
    WHERE con.conrelid = 'tenant_cross_border_matches'::regclass
      AND con.contype = 'u'
      AND pg_get_constraintdef(con.oid) =
          'UNIQUE (tenant_id, process_id, profile_version)';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE tenant_cross_border_matches DROP CONSTRAINT %I',
            constraint_name
        );
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_cross_border_match_act
    ON tenant_cross_border_matches (tenant_id, act_id, profile_version)
    WHERE act_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_cross_border_match_act
    ON tenant_cross_border_matches (act_id, computed_at DESC);
