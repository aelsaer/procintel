-- 27_pre_tender_signals.sql
-- Evidence-backed demand signals observed before a formal tender notice.

CREATE TABLE IF NOT EXISTS procurement_signals (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_type         TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT,
    buyer_entity_id     UUID REFERENCES entities(id),
    source_record_id    UUID NOT NULL REFERENCES source_records(id),
    source_url          TEXT,
    source_identifier   TEXT,
    publication_date    DATE,
    expected_notice_date DATE,
    estimated_value     NUMERIC(20,2),
    currency            CHAR(3) NOT NULL DEFAULT 'EUR',
    cpv_codes           TEXT[] NOT NULL DEFAULT '{}',
    nuts_codes          TEXT[] NOT NULL DEFAULT '{}',
    confidence          NUMERIC(5,4) NOT NULL,
    evidence            JSONB NOT NULL DEFAULT '{}',
    linked_process_id   UUID REFERENCES procurement_processes(id),
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_record_id, signal_type),
    CHECK (signal_type IN (
        'PROCUREMENT_PLAN', 'CONSULTATION', 'BUDGET_APPROVAL',
        'COMMITTEE_MINUTES', 'EXPIRING_CONTRACT', 'EARLY_REQUEST'
    )),
    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS ix_procurement_signals_feed
    ON procurement_signals (publication_date DESC, signal_type)
    WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS ix_procurement_signals_buyer
    ON procurement_signals (buyer_entity_id, publication_date DESC)
    WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS ix_procurement_signals_cpv
    ON procurement_signals USING GIN (cpv_codes);

GRANT SELECT, INSERT, UPDATE, DELETE ON procurement_signals TO procintel_app;
