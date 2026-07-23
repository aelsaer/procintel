-- Strict lexical title search uses the same accent/case normalization as the
-- API and pg_trgm for substring/regular-expression acceleration.
CREATE OR REPLACE FUNCTION procintel_normalize_lexical(value TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
RETURN regexp_replace(
    translate(
        lower(COALESCE(value, '')),
        'άέήίόύώϊΐϋΰς',
        'αεηιουωιιυυσ'
    ),
    '[^a-z0-9α-ω]+',
    ' ',
    'g'
);

CREATE OR REPLACE FUNCTION procintel_taxonomy_match(
    act_uuid UUID,
    act_title TEXT,
    cpv_likes TEXT[],
    keyword_patterns TEXT[],
    match_all BOOLEAN
)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
PARALLEL SAFE
RETURN
    (CARDINALITY(cpv_likes) = 0 AND CARDINALITY(keyword_patterns) = 0)
    OR (
        NOT match_all
        AND (
            (
                CARDINALITY(cpv_likes) > 0
                AND EXISTS (
                    SELECT 1
                    FROM act_cpv_codes code
                    WHERE code.act_id = act_uuid
                      AND code.cpv_code LIKE ANY(cpv_likes)
                )
            )
            OR (
                CARDINALITY(keyword_patterns) > 0
                AND procintel_normalize_lexical(act_title) ~* ANY(keyword_patterns)
            )
        )
    )
    OR (
        match_all
        AND (
            CARDINALITY(cpv_likes) = 0
            OR EXISTS (
                SELECT 1
                FROM act_cpv_codes code
                WHERE code.act_id = act_uuid
                  AND code.cpv_code LIKE ANY(cpv_likes)
            )
        )
        AND (
            CARDINALITY(keyword_patterns) = 0
            OR procintel_normalize_lexical(act_title) ~* ANY(keyword_patterns)
        )
    );

CREATE INDEX IF NOT EXISTS ix_procurement_acts_title_lexical_trgm
ON procurement_acts
USING GIN (
    (
        regexp_replace(
            translate(
                lower(COALESCE(title, '')),
                'άέήίόύώϊΐϋΰς',
                'αεηιουωιιυυσ'
            ),
            '[^a-z0-9α-ω]+',
            ' ',
            'g'
        )
    ) gin_trgm_ops
);
