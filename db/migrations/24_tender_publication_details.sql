-- Public-facing tender details preserved from provider payloads.
-- source_details contains a curated, non-authoritative subset used for
-- summaries; the complete immutable payload remains in source_records.
ALTER TABLE procurement_acts
    ADD COLUMN IF NOT EXISTS submission_deadline TIMESTAMPTZ;

ALTER TABLE procurement_acts
    ADD COLUMN IF NOT EXISTS source_details JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_acts_submission_deadline
    ON procurement_acts (submission_deadline)
    WHERE submission_deadline IS NOT NULL;
