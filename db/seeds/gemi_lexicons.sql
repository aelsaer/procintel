-- gemi_lexicons.sql
-- ΓΕΜΗ legal-form / company-status reference data (schema:
-- db/migrations/11_gemi_lexicons.sql). Mirrors
-- services/ingestion/connectors/gemi/lexicon.py's Python dicts — keep both
-- in sync by hand if either changes.
-- Safe to re-run (ON CONFLICT DO NOTHING).

INSERT INTO gemi_legal_forms (code, label_el) VALUES
    ('AE', 'Ανώνυμη Εταιρεία'),
    ('EPE', 'Εταιρεία Περιορισμένης Ευθύνης'),
    ('IKE', 'Ιδιωτική Κεφαλαιουχική Εταιρεία'),
    ('OE', 'Ομόρρυθμη Εταιρεία'),
    ('EE', 'Ετερόρρυθμη Εταιρεία'),
    ('SOLE_PROPRIETORSHIP', 'Ατομική Επιχείρηση'),
    ('CIVIL_LAW_PARTNERSHIP', 'Κοινωνία Αστικού Δικαίου'),
    ('COOPERATIVE', 'Συνεταιρισμός'),
    ('JOINT_VENTURE', 'Κοινοπραξία'),
    ('FOREIGN_BRANCH', 'Υποκατάστημα Αλλοδαπής Εταιρείας')
ON CONFLICT (code) DO NOTHING;

INSERT INTO gemi_company_statuses (code, label_el, is_stable) VALUES
    ('ACTIVE', 'Ενεργή', TRUE),
    ('SUSPENDED', 'Σε Αναστολή', FALSE),
    ('IN_LIQUIDATION', 'Υπό Εκκαθάριση', FALSE),
    ('DISSOLVED', 'Διαλυμένη', FALSE),
    ('DEREGISTERED', 'Διαγραμμένη', FALSE),
    ('MERGED', 'Συγχωνευθείσα', FALSE)
ON CONFLICT (code) DO NOTHING;
