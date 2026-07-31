-- Official Eurostat TERCET postal-code to NUTS correspondence. The source is
-- a lookup, not a postal-boundary polygon dataset, so the model keeps that
-- distinction explicit.

CREATE TABLE IF NOT EXISTS postal_code_nuts (
    country_code            CHAR(2) NOT NULL,
    postal_code             TEXT NOT NULL,
    nuts_code               TEXT NOT NULL REFERENCES nuts_areas(code),
    classification_version  TEXT NOT NULL,
    source_record_id        UUID NOT NULL REFERENCES source_records(id),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (
        country_code,
        postal_code,
        nuts_code,
        classification_version
    )
);

CREATE INDEX IF NOT EXISTS ix_postal_code_nuts_lookup
    ON postal_code_nuts (country_code, postal_code);
