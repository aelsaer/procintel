-- 04_documents_and_provenance.sql
-- Document store and field-level provenance — the backing data for the
-- "evidence drawer" (spec §31.8) shown on every metric and field in the UI.
-- Spec refs: description.txt §15.11, §23, §24.

CREATE TABLE documents (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id                   UUID REFERENCES procurement_acts(id),
    source_record_id           UUID REFERENCES source_records(id),
    document_type                TEXT,                       -- e.g. DIAVGEIA_PDF, KHMDHS_ATTACHMENT, TENDER_DOC
    title                          TEXT,
    source_url                       TEXT,
    object_uri                         TEXT NOT NULL,          -- s3://documents/...
    mime_type                            TEXT,
    file_size                              BIGINT,
    sha256                                   TEXT NOT NULL,
    text_extraction_status                     TEXT NOT NULL DEFAULT 'PENDING',
        -- PENDING | TEXT_LAYER | OCR_REQUIRED | OCR_DONE | FAILED
    page_count                                   INTEGER,
    language                                       TEXT,
    created_at                                       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_documents_act ON documents (act_id);
CREATE UNIQUE INDEX uq_documents_sha256 ON documents (sha256);

-- ---------------------------------------------------------------------------
-- field_provenance (§24): every derived/canonical field can point back to
-- exactly which source record, path and extraction method produced it. This
-- is the mechanism behind the evidence drawer and the API's `provenance`
-- block (§30.4), and is reused verbatim by procurement_360 (db/marts).
-- ---------------------------------------------------------------------------
CREATE TABLE field_provenance (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type           TEXT NOT NULL,             -- e.g. procurement_acts, procurement_processes, entities
    object_id               UUID NOT NULL,
    field_name                TEXT NOT NULL,
    source_record_id            UUID NOT NULL REFERENCES source_records(id),
    source_path                   TEXT,                -- e.g. "$.totalCostWithVAT"
    extraction_method               TEXT NOT NULL,       -- DIRECT_FIELD_MAPPING | REGEX | OCR | LLM_SUGGESTED | COMPUTED
    confidence                        NUMERIC(5,4) NOT NULL,
    observed_at                         TIMESTAMPTZ NOT NULL,
    value_hash                            TEXT
);

CREATE INDEX ix_field_provenance_object ON field_provenance (object_type, object_id, field_name);
