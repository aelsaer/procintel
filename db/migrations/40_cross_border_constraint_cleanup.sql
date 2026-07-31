-- Locate the legacy unique constraint by definition because PostgreSQL
-- truncates long generated names.
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
