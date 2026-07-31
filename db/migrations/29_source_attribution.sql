-- Preserve explicit source attribution on every immutable raw record.
ALTER TABLE source_records
    ADD COLUMN IF NOT EXISTS attribution_text TEXT;

UPDATE source_records
SET attribution_text = CASE source_system
    WHEN 'KHMDHS' THEN 'ΚΗΜΔΗΣ Open Data, ΕΣΗΔΗΣ'
    WHEN 'DIAVGEIA' THEN 'Πρόγραμμα Διαύγεια Open Data'
    WHEN 'GEMI' THEN 'Open Data ΓΕΜΗ'
    WHEN 'ANAPTYXI' THEN 'ΑΝΑΠΤΥΞΗ.gov.gr Open Data'
    WHEN 'TED' THEN 'Tenders Electronic Daily, Publications Office of the European Union'
    WHEN 'MEF' THEN 'Μητρώο Επιχορηγούμενων Φορέων'
    WHEN 'CKAN' THEN 'Source dataset attribution; see external_datasets'
    ELSE source_system
END
WHERE attribution_text IS NULL;

UPDATE source_records
SET license_code = 'UNCONFIRMED'
WHERE license_code IS NULL;

ALTER TABLE source_records
    ALTER COLUMN attribution_text SET NOT NULL;
ALTER TABLE source_records
    ALTER COLUMN license_code SET NOT NULL;

CREATE OR REPLACE FUNCTION procintel_source_attribution_default()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.license_code IS NULL OR btrim(NEW.license_code) = '' THEN
        NEW.license_code := 'UNCONFIRMED';
    END IF;
    IF NEW.attribution_text IS NULL OR btrim(NEW.attribution_text) = '' THEN
        NEW.attribution_text := CASE NEW.source_system
            WHEN 'KHMDHS' THEN 'ΚΗΜΔΗΣ Open Data, ΕΣΗΔΗΣ'
            WHEN 'DIAVGEIA' THEN 'Πρόγραμμα Διαύγεια Open Data'
            WHEN 'GEMI' THEN 'Open Data ΓΕΜΗ'
            WHEN 'ANAPTYXI' THEN 'ΑΝΑΠΤΥΞΗ.gov.gr Open Data'
            WHEN 'TED' THEN 'Tenders Electronic Daily, Publications Office of the European Union'
            WHEN 'MEF' THEN 'Μητρώο Επιχορηγούμενων Φορέων'
            WHEN 'CKAN' THEN 'Source dataset attribution; see external_datasets'
            ELSE NEW.source_system
        END;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_source_records_attribution ON source_records;
CREATE TRIGGER trg_source_records_attribution
BEFORE INSERT ON source_records
FOR EACH ROW
EXECUTE FUNCTION procintel_source_attribution_default();
