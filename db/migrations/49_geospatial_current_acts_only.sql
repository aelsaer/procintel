-- Keep identifier-only lifecycle evidence out of the geospatial work queue.
-- A real source payload reactivates the canonical act and changes its source
-- record, which creates a fresh job for the evidence-bearing version.

CREATE OR REPLACE FUNCTION enqueue_act_geospatial_enrichment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_current = TRUE
       AND NEW.act_type IN (
           'REQUEST', 'APPROVED_REQUEST', 'NOTICE', 'AWARD', 'CONTRACT',
           'TED_NOTICE'
       ) THEN
        INSERT INTO geospatial_enrichment_jobs (act_id, source_record_id)
        VALUES (NEW.id, NEW.source_record_id)
        ON CONFLICT (act_id, source_record_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enqueue_act_geospatial_enrichment ON procurement_acts;
CREATE TRIGGER trg_enqueue_act_geospatial_enrichment
AFTER INSERT OR UPDATE OF source_record_id ON procurement_acts
FOR EACH ROW EXECUTE FUNCTION enqueue_act_geospatial_enrichment();

CREATE OR REPLACE FUNCTION requeue_document_geospatial_enrichment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO geospatial_enrichment_jobs (act_id, source_record_id)
    SELECT d.act_id, a.source_record_id
    FROM documents d
    JOIN procurement_acts a ON a.id = d.act_id
    WHERE d.id = NEW.document_id
      AND a.is_current = TRUE
      AND a.act_type IN (
          'REQUEST', 'APPROVED_REQUEST', 'NOTICE', 'AWARD', 'CONTRACT',
          'TED_NOTICE'
      )
    ON CONFLICT (act_id, source_record_id) DO UPDATE
    SET status = 'QUEUED',
        available_at = now(),
        locked_at = NULL,
        locked_by = NULL,
        last_error = NULL,
        finished_at = NULL;
    RETURN NEW;
END;
$$;

COMMENT ON COLUMN geospatial_enrichment_jobs.status IS
    'QUEUED | RUNNING | SUCCEEDED | PARTIAL | NO_LOCATION | OUTSIDE_GREECE | SUPERSEDED | FAILED';

-- Evidence-only acts must not invalidate every tenant's opportunity scores.
-- Real ingestion updates the scored fields below and therefore still queues
-- a refresh when it reactivates a placeholder.
CREATE OR REPLACE FUNCTION enqueue_opportunity_scoring_for_new_act()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_current = TRUE THEN
        INSERT INTO opportunity_score_jobs (
            tenant_id, status, reason, requested_at, error
        )
        SELECT tenant_id, 'QUEUED', 'PROCUREMENT_CHANGED', now(), NULL
        FROM business_profiles
        ON CONFLICT (tenant_id) DO UPDATE SET
            status = 'QUEUED',
            reason = EXCLUDED.reason,
            requested_at = EXCLUDED.requested_at,
            error = NULL;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_procurement_act_score_queue ON procurement_acts;
CREATE TRIGGER trg_procurement_act_score_queue
AFTER INSERT OR UPDATE OF title, amount_gross, publication_date, end_date
ON procurement_acts FOR EACH ROW
EXECUTE FUNCTION enqueue_opportunity_scoring_for_new_act();
