-- 11_gemi_lexicons.sql
-- Reference tables for the ΓΕΜΗ legal-form/company-status vocabulary
-- (§18.2's entity_company_snapshots.legal_form_code/company_status
-- columns are free text with an implied canonical vocabulary — this makes
-- that vocabulary queryable directly in SQL, e.g. for a future UI
-- dropdown, rather than only living inside
-- services/ingestion/connectors/gemi/lexicon.py's Python dicts).
-- Populated by db/seeds/gemi_lexicons.sql. Keep both in sync by hand if
-- either changes — see lexicon.py's module docstring.
-- Spec refs: description.txt §18.2, §6.3.

CREATE TABLE gemi_legal_forms (
    code        TEXT PRIMARY KEY,   -- e.g. 'IKE', matches entity_company_snapshots.legal_form_code
    label_el    TEXT NOT NULL
);

CREATE TABLE gemi_company_statuses (
    code        TEXT PRIMARY KEY,   -- e.g. 'ACTIVE', matches entity_company_snapshots.company_status
    label_el    TEXT NOT NULL,
    is_stable   BOOLEAN NOT NULL DEFAULT FALSE  -- drives cache.py's refresh-interval choice (§18.3)
);
