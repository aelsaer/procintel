-- 02_identity_and_registry.sql
-- Canonical entities, the central identifier registry, name/address history,
-- ΓΕΜΗ temporal company snapshots, VIES validation history, and the entity
-- resolution review queue / reversible merge log.
-- Spec refs: description.txt §6.3-6.4, §7, §9, §18.2, §25.

-- ---------------------------------------------------------------------------
-- entities: PUBLIC_ORGANIZATION | COMPANY | CONSORTIUM | FUNDING_PROGRAM |
-- PROJECT | PERSON | GEOGRAPHIC_AREA (§6.3). PERSON rows are limited to what's
-- needed to represent a publicly published act (e.g. a signer) — see §41.2.
-- ---------------------------------------------------------------------------
CREATE TABLE entities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type         TEXT NOT NULL,
    canonical_name      TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    country_code        CHAR(2),
    status              TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE | MERGED
    merged_into_id       UUID REFERENCES entities(id),      -- set only when status = MERGED; reversible (clear both)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_entities_normalized_name
    ON entities USING gin (normalized_name gin_trgm_ops);

CREATE INDEX ix_entities_type ON entities (entity_type);

-- ---------------------------------------------------------------------------
-- entity_identifiers: the central identifier registry (§6.4, §7.1). Schemes:
-- ADAM, ADA, AFM, EU_VAT, GEMI, AAHT, CPV, NUTS, MIS_OPS, TED_NOTICE_ID,
-- SOURCE_NATIVE_ID. Confidence 1 + is_current enforces "no two entities share
-- a current, fully-confident identifier of the same scheme+country".
-- ---------------------------------------------------------------------------
CREATE TABLE entity_identifiers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id           UUID NOT NULL REFERENCES entities(id),
    scheme              TEXT NOT NULL,
    value_raw           TEXT NOT NULL,
    value_normalized    TEXT NOT NULL,
    country_code        CHAR(2),
    source_record_id    UUID REFERENCES source_records(id),
    confidence          NUMERIC(5,4) NOT NULL DEFAULT 1,
    identifier_valid    BOOLEAN NOT NULL DEFAULT TRUE,      -- e.g. false on failed AFM checksum
    match_eligibility   TEXT NOT NULL DEFAULT 'ELIGIBLE',   -- ELIGIBLE | RESTRICTED
    valid_from          TIMESTAMPTZ,
    valid_to            TIMESTAMPTZ,
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_entity_identifier_current
    ON entity_identifiers (scheme, COALESCE(country_code, ''), value_normalized)
    WHERE is_current = TRUE AND confidence = 1;

CREATE INDEX ix_entity_identifiers_entity ON entity_identifiers (entity_id);
CREATE INDEX ix_entity_identifiers_lookup ON entity_identifiers (scheme, value_normalized);

-- ---------------------------------------------------------------------------
-- entity_names: alternate/historical names (§9, §15.2). name_type e.g.
-- OFFICIAL | TRADE | FORMER | KHMDHS_RAW | DIAVGEIA_RAW.
-- ---------------------------------------------------------------------------
CREATE TABLE entity_names (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id           UUID NOT NULL REFERENCES entities(id),
    name                TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    name_search         TEXT,                                -- accent-folded, for search
    name_without_legal_form TEXT,
    name_type           TEXT NOT NULL,
    source_record_id    UUID REFERENCES source_records(id),
    valid_from          TIMESTAMPTZ,
    valid_to            TIMESTAMPTZ,
    is_current          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX ix_entity_names_entity ON entity_names (entity_id);
CREATE INDEX ix_entity_names_trgm ON entity_names USING gin (normalized_name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- entity_addresses (§15.3).
-- ---------------------------------------------------------------------------
CREATE TABLE entity_addresses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id           UUID NOT NULL REFERENCES entities(id),
    address_line        TEXT,
    postal_code         TEXT,
    municipality        TEXT,
    region               TEXT,
    nuts_code           TEXT,
    country_code        CHAR(2),
    geom                geometry(Point, 4326),
    source_record_id    UUID REFERENCES source_records(id),
    valid_from          TIMESTAMPTZ,
    valid_to            TIMESTAMPTZ,
    is_current          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX ix_entity_addresses_entity ON entity_addresses (entity_id);
CREATE INDEX ix_entity_addresses_geom ON entity_addresses USING gist (geom);

-- ---------------------------------------------------------------------------
-- entity_company_snapshots: ΓΕΜΗ temporal state (§18.2) — never overwritten,
-- so "what was the company's status when the contract was signed" is
-- answerable, not just "what is it today".
-- ---------------------------------------------------------------------------
CREATE TABLE entity_company_snapshots (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id               UUID NOT NULL REFERENCES entities(id),
    source_record_id        UUID REFERENCES source_records(id),
    official_name           TEXT,
    trade_name              TEXT,
    gemi_number             TEXT,
    vat_number               TEXT,
    legal_form               TEXT,
    legal_form_code          TEXT,
    company_status           TEXT,                            -- ACTIVE | SUSPENDED | DISSOLVED | ... (ΓΕΜΗ lexicon)
    gemi_office              TEXT,
    gemi_registration_date   DATE,
    kad_codes                TEXT[],
    municipality             TEXT,
    region                   TEXT,
    observed_at              TIMESTAMPTZ NOT NULL,
    valid_from                TIMESTAMPTZ NOT NULL,
    valid_to                  TIMESTAMPTZ,
    is_current                BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_company_snapshots_entity ON entity_company_snapshots (entity_id, is_current);

-- ---------------------------------------------------------------------------
-- entity_vies_checks: VIES is a validator, not a company profile (§3.9,
-- §7.2) — kept as an append-only check history, separate from identifiers.
-- ---------------------------------------------------------------------------
CREATE TABLE entity_vies_checks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id               UUID NOT NULL REFERENCES entities(id),
    country_code             CHAR(2) NOT NULL,
    national_number          TEXT NOT NULL,
    normalized_eu_vat        TEXT NOT NULL,
    checked_at                TIMESTAMPTZ NOT NULL,
    vies_valid                BOOLEAN,
    vies_response_hash        TEXT,
    source_record_id          UUID REFERENCES source_records(id),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_vies_checks_entity ON entity_vies_checks (entity_id, checked_at DESC);

-- ---------------------------------------------------------------------------
-- entity_match_candidates: the §25.4 review queue. Statuses per §8.2.
-- ---------------------------------------------------------------------------
CREATE TABLE entity_match_candidates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_a_id         UUID NOT NULL REFERENCES entities(id),
    entity_b_id         UUID NOT NULL REFERENCES entities(id),
    score               NUMERIC(5,4) NOT NULL,
    score_breakdown     JSONB NOT NULL,               -- per-feature contributions, §25.3
    blocking_reason     TEXT NOT NULL,                 -- which candidate-generation rule produced this pair, §25.1
    status              TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
        -- CONFIRMED_SOURCE_LINK | CONFIRMED_IDENTIFIER | AUTO_MATCHED |
        -- PENDING_REVIEW | REJECTED | MANUALLY_CONFIRMED | MANUALLY_SPLIT
    reviewed_by          UUID,
    reviewed_at           TIMESTAMPTZ,
    review_notes          TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (entity_a_id <> entity_b_id),
    UNIQUE (entity_a_id, entity_b_id)
);

CREATE INDEX ix_match_candidates_status ON entity_match_candidates (status) WHERE status = 'PENDING_REVIEW';

-- ---------------------------------------------------------------------------
-- entity_merge_log: audit trail behind entities.merged_into_id. Every merge
-- must be reversible (§25.4) — revert by clearing merged_into_id/status on
-- the merged entity and stamping reverted_at/reverted_by here.
-- ---------------------------------------------------------------------------
CREATE TABLE entity_merge_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    surviving_entity_id  UUID NOT NULL REFERENCES entities(id),
    merged_entity_id     UUID NOT NULL REFERENCES entities(id),
    match_candidate_id    UUID REFERENCES entity_match_candidates(id),
    merge_reason           TEXT NOT NULL,
    evidence               JSONB NOT NULL,
    performed_by            TEXT NOT NULL,
    performed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    reverted_at              TIMESTAMPTZ,
    reverted_by               TEXT
);

CREATE INDEX ix_merge_log_surviving ON entity_merge_log (surviving_entity_id);
