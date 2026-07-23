-- 05_funding_and_external_sources.sql
-- Funding (ΑΝΑΠΤΥΞΗ.gov.gr), TED, and Μητρώο Επιχορηγούμενων Φορέων (ΜΕΦ).
-- Spec refs: description.txt §15.12-15.13, §19, §20, §21.

-- ---------------------------------------------------------------------------
-- funding_projects (§15.12). program_period distinguishes the three ΑΝΑΠΤΥΞΗ
-- adapters (§19.3) even though they converge on this one schema.
-- ---------------------------------------------------------------------------
CREATE TABLE funding_projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mis_ops_code           TEXT,
    program_code             TEXT,
    program_period              TEXT,                  -- ANAPTYXI_2007_2013 | ANAPTYXI_2014_2020 | ANAPTYXI_2021_2027
    title                          TEXT NOT NULL,
    beneficiary_id                    UUID REFERENCES entities(id),
    budget                              NUMERIC(20,2),
    contracted_amount                     NUMERIC(20,2),
    paid_amount                             NUMERIC(20,2),
    currency                                  CHAR(3) DEFAULT 'EUR',
    start_date                                  DATE,
    end_date                                      DATE,
    status                                          TEXT,
    source_record_id                                  UUID REFERENCES source_records(id)
);

CREATE INDEX ix_funding_projects_mis ON funding_projects (mis_ops_code);
CREATE INDEX ix_funding_projects_beneficiary ON funding_projects (beneficiary_id);

-- ---------------------------------------------------------------------------
-- funding_links (§15.13): contract <-> funding project. link_method mirrors
-- the §19.2 join hierarchy: MIS_OPS_EXACT | AFM_TITLE_PERIOD | ADAM_ADA_REF |
-- FUZZY_TITLE_AMOUNT_REGION.
-- ---------------------------------------------------------------------------
CREATE TABLE funding_links (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id                UUID NOT NULL REFERENCES procurement_acts(id),
    funding_project_id      UUID NOT NULL REFERENCES funding_projects(id),
    link_method                TEXT NOT NULL,
    confidence                    NUMERIC(5,4) NOT NULL,
    evidence                        JSONB NOT NULL,
    created_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (act_id, funding_project_id)
);

CREATE INDEX ix_funding_links_project ON funding_links (funding_project_id);

-- ---------------------------------------------------------------------------
-- ted_notice_details: 1:1 supplement to a procurement_acts row of type
-- TED_NOTICE, carrying eForms-version-specific structured fields the generic
-- act table doesn't model (§21.1-21.2). Every field's source_path/parser
-- version/confidence is additionally tracked per-field in field_provenance.
-- ---------------------------------------------------------------------------
CREATE TABLE ted_notice_details (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id                UUID NOT NULL UNIQUE REFERENCES procurement_acts(id),
    ted_notice_id           TEXT NOT NULL,
    publication_number         TEXT,
    raw_format                    TEXT NOT NULL,          -- XML | JSON
    notice_type                     TEXT,
    eforms_version                    TEXT,                 -- NULL/'' => legacy forms
    parser_version                      TEXT NOT NULL,
    parse_confidence                      NUMERIC(5,4) NOT NULL DEFAULT 1,
    buyer_raw                               JSONB,
    supplier_raw                              JSONB,
    lots                                        JSONB,
    country_code                                  CHAR(2),
    nuts_codes                                      TEXT[],
    related_notice_ids                                TEXT[],
    source_record_id                                    UUID REFERENCES source_records(id)
);

CREATE UNIQUE INDEX uq_ted_notice_id ON ted_notice_details (ted_notice_id);

-- ---------------------------------------------------------------------------
-- mef_organizations / mef_expenses (§3.5, §20). ΜΕΦ is not a universal public
-- payments registry — coverage/last-updated must always accompany any metric
-- built on it (enforced at the analytics layer, db/marts).
-- ---------------------------------------------------------------------------
CREATE TABLE mef_organizations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id             UUID REFERENCES entities(id),    -- resolved link, nullable until matched
    source_native_id        TEXT,
    name                       TEXT,
    afm_raw                      TEXT,
    source_record_id               UUID REFERENCES source_records(id),
    created_at                       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mef_expenses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mef_organization_id    UUID NOT NULL REFERENCES mef_organizations(id),
    recipient_entity_id      UUID REFERENCES entities(id),  -- resolved link, nullable until matched
    recipient_afm_raw          TEXT,
    amount                        NUMERIC(20,2),
    vat_amount                       NUMERIC(20,2),
    expense_date                        DATE,
    related_ada_raw                       TEXT,
    linked_act_id                            UUID REFERENCES procurement_acts(id),
    link_method                                TEXT,          -- ADA_AND_AFM | ADA_AND_BUYER | AFM_AMOUNT_DATE | AFM_ONLY_CANDIDATE
    confidence                                    NUMERIC(5,4),
        -- indicative tiers per §20.2: ADA+AFM=0.99, ADA+buyer=0.97, AFM+amount+-5d=0.90, AFM only=candidate (no link row)
    source_record_id                                UUID REFERENCES source_records(id),
    created_at                                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_mef_expenses_org ON mef_expenses (mef_organization_id);
CREATE INDEX ix_mef_expenses_linked_act ON mef_expenses (linked_act_id) WHERE linked_act_id IS NOT NULL;

COMMENT ON COLUMN mef_expenses.linked_act_id IS
    'UI must phrase this as "a declared expense/payment order was found that possibly relates to the contract", never "the contract was paid" (§20.3), unless source semantics fully prove it.';
