-- One immutable document can be referenced by many procurement acts. Keep the
-- original documents.act_id for backward compatibility and make this link
-- table the authoritative act/document relationship.

CREATE TABLE IF NOT EXISTS document_act_links (
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    act_id UUID NOT NULL REFERENCES procurement_acts(id) ON DELETE CASCADE,
    source_record_id UUID REFERENCES source_records(id),
    document_type TEXT,
    title TEXT,
    source_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, act_id)
);

INSERT INTO document_act_links (
    document_id, act_id, source_record_id, document_type, title, source_url
)
SELECT id, act_id, source_record_id, document_type, title, source_url
  FROM documents
 WHERE act_id IS NOT NULL
ON CONFLICT (document_id, act_id) DO UPDATE SET
    source_record_id = COALESCE(EXCLUDED.source_record_id, document_act_links.source_record_id),
    document_type = COALESCE(EXCLUDED.document_type, document_act_links.document_type),
    title = COALESCE(EXCLUDED.title, document_act_links.title),
    source_url = COALESCE(EXCLUDED.source_url, document_act_links.source_url);

CREATE INDEX IF NOT EXISTS ix_document_act_links_act
    ON document_act_links (act_id, document_id);

CREATE INDEX IF NOT EXISTS ix_document_act_links_source_record
    ON document_act_links (source_record_id)
    WHERE source_record_id IS NOT NULL;

CREATE OR REPLACE FUNCTION sync_document_primary_act_link()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.act_id IS NOT NULL THEN
        INSERT INTO document_act_links (
            document_id, act_id, source_record_id, document_type, title, source_url
        )
        VALUES (
            NEW.id, NEW.act_id, NEW.source_record_id,
            NEW.document_type, NEW.title, NEW.source_url
        )
        ON CONFLICT (document_id, act_id) DO UPDATE SET
            source_record_id = COALESCE(EXCLUDED.source_record_id, document_act_links.source_record_id),
            document_type = COALESCE(EXCLUDED.document_type, document_act_links.document_type),
            title = COALESCE(EXCLUDED.title, document_act_links.title),
            source_url = COALESCE(EXCLUDED.source_url, document_act_links.source_url);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_sync_document_primary_act_link ON documents;
CREATE TRIGGER trg_sync_document_primary_act_link
AFTER INSERT OR UPDATE OF act_id, source_record_id, document_type, title, source_url
ON documents
FOR EACH ROW EXECUTE FUNCTION sync_document_primary_act_link();

CREATE OR REPLACE FUNCTION requeue_document_link_geospatial_enrichment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO geospatial_enrichment_jobs (act_id, source_record_id)
    SELECT NEW.act_id, act.source_record_id
      FROM procurement_acts act
     WHERE act.id = NEW.act_id
       AND act.is_current = TRUE
       AND act.act_type IN (
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

DROP TRIGGER IF EXISTS trg_document_act_link_geospatial ON document_act_links;
CREATE TRIGGER trg_document_act_link_geospatial
AFTER INSERT OR UPDATE OF act_id ON document_act_links
FOR EACH ROW EXECUTE FUNCTION requeue_document_link_geospatial_enrichment();
