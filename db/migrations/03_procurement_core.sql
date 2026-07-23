-- 03_procurement_core.sql
-- The procurement lifecycle graph: processes, stable process membership/merge
-- history, published acts, act identifiers, act-to-act links, parties, CPV,
-- locations and lots.
-- Spec refs: description.txt §6.1-6.2, §15.4-15.10, §16.5-16.6, §26.

-- ---------------------------------------------------------------------------
-- procurement_processes: the business event (§6.1). public_id is the stable,
-- externally-visible identifier and must never change, even across merges
-- (§16.6) — a merged process is kept as a row with record_status = MERGED so
-- its public_id still resolves (redirected to the survivor by the API).
-- ---------------------------------------------------------------------------
CREATE TABLE procurement_processes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id               TEXT UNIQUE NOT NULL,
    title                   TEXT,
    normalized_title        TEXT,
    lifecycle_status        TEXT NOT NULL DEFAULT 'DISCOVERED',
        -- DISCOVERED | REQUESTED | APPROVED | PUBLISHED | AWARDED | CONTRACTED |
        -- IN_EXECUTION | MODIFIED | COMPLETED | CANCELLED | UNKNOWN | PARTIAL_LIFECYCLE  (§26.1)
    record_status            TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE | MERGED
    merged_into_process_id    UUID REFERENCES procurement_processes(id),
    buyer_entity_id           UUID REFERENCES entities(id),
    primary_cpv_code          TEXT,                             -- soft reference to db/migrations/06_reference_geo.sql cpv_codes
    estimated_value            NUMERIC(20,2),
    awarded_value               NUMERIC(20,2),
    current_contract_value       NUMERIC(20,2),
    currency                     CHAR(3) DEFAULT 'EUR',
    first_observed_at             TIMESTAMPTZ,
    last_observed_at              TIMESTAMPTZ,
    created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_processes_buyer ON procurement_processes (buyer_entity_id);
CREATE INDEX ix_processes_cpv ON procurement_processes (primary_cpv_code);
CREATE INDEX ix_processes_title_trgm ON procurement_processes USING gin (normalized_title gin_trgm_ops);
CREATE INDEX ix_processes_active_status ON procurement_processes (lifecycle_status) WHERE record_status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- process_members: explicit membership of an act in a process. Avoids the
-- "first ΑΔΑΜ = process_id" anti-pattern (§16.6) — process_id is an internal
-- UUID from the start, and membership can grow as adamChain expands.
-- ---------------------------------------------------------------------------
CREATE TABLE process_members (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id           UUID NOT NULL REFERENCES procurement_processes(id),
    act_id                UUID NOT NULL,             -- FK added after procurement_acts exists, see below
    added_via              TEXT NOT NULL,              -- ADAMCHAIN | LINKAGE_ENGINE | MANUAL | MERGE
    added_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (process_id, act_id)
);

-- ---------------------------------------------------------------------------
-- process_merge_log: audit trail behind procurement_processes.merged_into_process_id.
-- Mirrors entity_merge_log — reversible by clearing the pointer + record_status.
-- ---------------------------------------------------------------------------
CREATE TABLE process_merge_log (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    surviving_process_id      UUID NOT NULL REFERENCES procurement_processes(id),
    merged_process_id          UUID NOT NULL REFERENCES procurement_processes(id),
    merge_reason                 TEXT NOT NULL,               -- e.g. "adamChain revealed shared REQUEST act"
    evidence                      JSONB NOT NULL,
    performed_by                   TEXT NOT NULL,
    performed_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    reverted_at                       TIMESTAMPTZ,
    reverted_by                        TEXT
);

-- ---------------------------------------------------------------------------
-- procurement_acts: each published act is an independent record (§6.2).
-- act_type: REQUEST | APPROVED_REQUEST | NOTICE | AWARD | CONTRACT |
-- AMENDMENT | CANCELLATION | PAYMENT | DIAVGEIA_DECISION | TED_NOTICE.
-- ---------------------------------------------------------------------------
CREATE TABLE procurement_acts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id           UUID REFERENCES procurement_processes(id),
    act_type              TEXT NOT NULL,
    title                  TEXT,
    normalized_title        TEXT,
    publication_date         DATE,
    submission_date           DATE,
    decision_date              DATE,
    start_date                  DATE,
    end_date                     DATE,
    status                        TEXT,                       -- source-reported status of this specific act
    amount_net                     NUMERIC(20,2),
    vat_amount                      NUMERIC(20,2),
    amount_gross                     NUMERIC(20,2),
    currency                          CHAR(3) DEFAULT 'EUR',
    procedure_type                     TEXT,
    agreement_type                      TEXT NOT NULL DEFAULT 'STANDARD', -- STANDARD | FRAMEWORK_AGREEMENT | CALL_OFF (§26.3)
    framework_ceiling_amount              NUMERIC(20,2),                   -- max value of a framework; never counted as realized spend
    source_record_id                       UUID NOT NULL REFERENCES source_records(id),
    is_current                               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE process_members
    ADD CONSTRAINT fk_process_members_act FOREIGN KEY (act_id) REFERENCES procurement_acts(id);

CREATE INDEX ix_acts_process ON procurement_acts (process_id);
CREATE INDEX ix_acts_type ON procurement_acts (act_type);
CREATE INDEX ix_acts_source_record ON procurement_acts (source_record_id);

-- ---------------------------------------------------------------------------
-- act_identifiers (§15.6). scheme values match entity_identifiers schemes
-- where applicable (ADAM, ADA, TED_NOTICE_ID, SOURCE_NATIVE_ID, MIS_OPS...).
-- ---------------------------------------------------------------------------
CREATE TABLE act_identifiers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id               UUID NOT NULL REFERENCES procurement_acts(id),
    scheme                TEXT NOT NULL,
    value_raw               TEXT NOT NULL,
    value_normalized          TEXT NOT NULL,
    source_record_id            UUID REFERENCES source_records(id),
    confidence                    NUMERIC(5,4) NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX uq_act_identifier ON act_identifiers (scheme, value_normalized);
CREATE INDEX ix_act_identifiers_act ON act_identifiers (act_id);

-- ---------------------------------------------------------------------------
-- act_links (§15.7, §8). link_method e.g. ADAMCHAIN | EXACT_IDENTIFIER |
-- MULTI_ATTRIBUTE | FUZZY | DIAVGEIA_SEARCH_MATCH | MANUAL.
-- ---------------------------------------------------------------------------
CREATE TABLE act_links (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_act_id          UUID NOT NULL REFERENCES procurement_acts(id),
    to_act_id             UUID NOT NULL REFERENCES procurement_acts(id),
    link_type              TEXT NOT NULL,
        -- APPROVES | ANNOUNCES | AWARDS | EXECUTES | AMENDS | EXTENDS | CANCELS |
        -- PAYS | FUNDED_BY | PUBLISHED_AS | SUPERSEDES | RELATED_TO
    link_method              TEXT NOT NULL,
    confidence                  NUMERIC(5,4) NOT NULL,
    evidence                      JSONB NOT NULL,
    created_by                      TEXT NOT NULL,
    reviewed_by                       UUID,
    created_at                          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (from_act_id, to_act_id, link_type)
);

CREATE INDEX ix_act_links_from ON act_links (from_act_id);
CREATE INDEX ix_act_links_to ON act_links (to_act_id);

-- ---------------------------------------------------------------------------
-- procurement_lots (§26.2) — declared before act_parties/act_cpv_codes so
-- they can reference it.
-- ---------------------------------------------------------------------------
CREATE TABLE procurement_lots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id           UUID NOT NULL REFERENCES procurement_processes(id),
    source_lot_id          TEXT,
    title                    TEXT,
    estimated_value            NUMERIC(20,2),
    awarded_value                NUMERIC(20,2),
    currency                       CHAR(3) DEFAULT 'EUR',
    status                           TEXT
);

CREATE INDEX ix_lots_process ON procurement_lots (process_id);

-- ---------------------------------------------------------------------------
-- act_parties (§15.8). Roles: BUYER | CONTRACTING_AUTHORITY | SUPPLIER |
-- CONTRACTOR | CONSORTIUM_MEMBER | BENEFICIARY | FUNDING_AUTHORITY | DONOR |
-- RECIPIENT | SIGNER_ORGANIZATION | SIGNER_PERSON. SIGNER_PERSON links a
-- minimal-field PERSON entity (§41.2) distinct from the deciding
-- organizational unit (SIGNER_ORGANIZATION) — see Διαύγεια source mapping.
-- ---------------------------------------------------------------------------
CREATE TABLE act_parties (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id                UUID NOT NULL REFERENCES procurement_acts(id),
    entity_id              UUID NOT NULL REFERENCES entities(id),
    party_role               TEXT NOT NULL,
    lot_id                     UUID REFERENCES procurement_lots(id),
    amount                       NUMERIC(20,2),
    currency                       CHAR(3) DEFAULT 'EUR',
    source_record_id                 UUID REFERENCES source_records(id)
);

CREATE INDEX ix_act_parties_act ON act_parties (act_id);
CREATE INDEX ix_act_parties_entity ON act_parties (entity_id, party_role);

-- ---------------------------------------------------------------------------
-- act_cpv_codes (§15.9).
-- ---------------------------------------------------------------------------
CREATE TABLE act_cpv_codes (
    act_id              UUID NOT NULL REFERENCES procurement_acts(id),
    cpv_code             TEXT NOT NULL,               -- soft reference to cpv_codes (06_reference_geo.sql)
    lot_id                 UUID REFERENCES procurement_lots(id),
    is_primary               BOOLEAN NOT NULL DEFAULT FALSE,
    source_record_id           UUID REFERENCES source_records(id),
    PRIMARY KEY (act_id, cpv_code)
);

-- ---------------------------------------------------------------------------
-- act_locations (§15.10) — execution location, distinct from buyer/supplier
-- registered address in entity_addresses.
-- ---------------------------------------------------------------------------
CREATE TABLE act_locations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id                UUID NOT NULL REFERENCES procurement_acts(id),
    nuts_code               TEXT,                       -- soft reference to nuts_areas (06_reference_geo.sql)
    municipality_code         TEXT,
    postal_code                 TEXT,
    place_text                    TEXT,
    geom                            geometry(Geometry, 4326),
    source_record_id                  UUID REFERENCES source_records(id)
);

CREATE INDEX ix_act_locations_act ON act_locations (act_id);
CREATE INDEX ix_act_locations_geom ON act_locations USING gist (geom);
