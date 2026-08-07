-- Early installations created schema_migrations before the tracked runner
-- declared filename as its primary key. Enforce the runner's invariant so a
-- restored database cannot record the same migration more than once.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'schema_migrations'::regclass
          AND contype = 'p'
    ) THEN
        IF EXISTS (
            SELECT filename
            FROM schema_migrations
            GROUP BY filename
            HAVING COUNT(*) > 1
        ) THEN
            RAISE EXCEPTION
                'schema_migrations contains duplicate filenames; deduplicate before migration 46';
        END IF;
        ALTER TABLE schema_migrations
            ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (filename);
    END IF;
END
$$;
