-- 18_competitor_participations.sql
-- Evidence-backed participation facts used by competitor intelligence.
-- Inferred competitors are deliberately not persisted here: this table is
-- reserved for an official winner/participant or a role extracted from a
-- stored procurement document with traceable evidence.

CREATE TABLE process_participations (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id               UUID NOT NULL REFERENCES procurement_processes(id),
    act_id                   UUID REFERENCES procurement_acts(id),
    entity_id                UUID REFERENCES entities(id),
    participant_name_raw     TEXT,
    participant_afm_raw      TEXT,
    participation_role       TEXT NOT NULL CHECK (
        participation_role IN ('BIDDER', 'WINNER', 'CONSORTIUM_MEMBER')
    ),
    evidence_type            TEXT NOT NULL CHECK (
        evidence_type IN ('OFFICIAL_SOURCE', 'DOCUMENT_EXTRACTED')
    ),
    confidence               NUMERIC(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source_record_id         UUID REFERENCES source_records(id),
    document_id              UUID REFERENCES documents(id),
    source_page              INTEGER CHECK (source_page IS NULL OR source_page > 0),
    evidence                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_key             TEXT UNIQUE NOT NULL,
    observed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        entity_id IS NOT NULL
        OR NULLIF(BTRIM(participant_name_raw), '') IS NOT NULL
        OR NULLIF(BTRIM(participant_afm_raw), '') IS NOT NULL
    )
);

CREATE INDEX ix_process_participations_process
    ON process_participations (process_id, participation_role);
CREATE INDEX ix_process_participations_entity
    ON process_participations (entity_id, participation_role)
    WHERE entity_id IS NOT NULL;
CREATE INDEX ix_process_participations_document
    ON process_participations (document_id, source_page)
    WHERE document_id IS NOT NULL;

