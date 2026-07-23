-- 13_document_pages.sql
-- Per-page extracted text for documents (spec §23.1's "page segmentation"
-- and "indexing" steps). documents itself (04_documents_and_provenance.sql)
-- has no page-level text column — this is the missing piece full-text
-- search over document content needs. Postgres tsvector/GIN is used
-- directly, consistent with the "Postgres is enough for MVP" stance
-- already applied to /v1/search over procurement_acts.title.

CREATE TABLE document_pages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id),
    page_number     INTEGER NOT NULL,
    text            TEXT NOT NULL DEFAULT '',
    text_search     TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    extraction_method TEXT NOT NULL, -- TEXT_LAYER | OCR
    ocr_mean_confidence NUMERIC(5,2), -- NULL when extraction_method = TEXT_LAYER
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_document_pages_document_page ON document_pages (document_id, page_number);
CREATE INDEX ix_document_pages_text_search ON document_pages USING GIN (text_search);
