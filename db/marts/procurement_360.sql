-- procurement_360.sql
-- THE unified per-procurement read model: one row per procurement_processes.id,
-- aggregating everything linked to that process across all source systems
-- (ΚΗΜΔΗΣ acts/identifiers, Διαύγεια decisions, ΓΕΜΗ company snapshots, ΑΝΑΠΤΥΞΗ
-- funding, TED notices, VIES checks, ΜΕΦ expense signals, geography), plus a
-- data-quality/confidence summary. This is the concrete answer to "a common
-- data schema for each procurement that includes information from all the
-- platforms" — it sits on top of, and never replaces, the normalized tables
-- in db/migrations/*, which remain the source of truth for provenance,
-- temporal history and reversible entity/process merges.
--
-- Spec refs: description.txt §5.2-5.7, §6, §30.4 (response shape), §37
-- (refresh pipeline), §47 (worked example this view is meant to answer).
--
-- Freshness strategy: defined here as a plain VIEW so it is always correct
-- relative to the canonical tables. Once query volume requires it, convert to
-- a MATERIALIZED VIEW and refresh only the affected process_id(s) — driven by
-- the domain-event chain in §37 (source record change -> canonical update ->
-- mart invalidation), not a blanket REFRESH MATERIALIZED VIEW. Do not
-- materialize until that need is demonstrated (matches the "Postgres graph is
-- enough for MVP" stance in §10-11).

CREATE OR REPLACE VIEW procurement_360 AS
WITH act_identifiers_by_act AS (
    SELECT
        act_id,
        jsonb_object_agg(scheme, values_for_scheme) AS identifiers
    FROM (
        SELECT act_id, scheme, jsonb_agg(DISTINCT value_normalized) AS values_for_scheme
        FROM act_identifiers
        GROUP BY act_id, scheme
    ) grouped
    GROUP BY act_id
),

-- This CTE is referenced by both the final row and lifecycle confidence.
-- Force inlining so a process_id predicate reaches ix_acts_process instead of
-- materializing every act and identifier in the database for one detail page.
acts_agg AS NOT MATERIALIZED (
    SELECT
        a.process_id,
        jsonb_agg(
            jsonb_build_object(
                'act_id', a.id,
                'act_type', a.act_type,
                'title', a.title,
                'procedure_type', a.procedure_type,
                'agreement_type', a.agreement_type,
                'framework_ceiling_amount', a.framework_ceiling_amount,
                'publication_date', a.publication_date,
                'submission_date', a.submission_date,
                'submission_deadline', a.submission_deadline,
                'decision_date', a.decision_date,
                'start_date', a.start_date,
                'end_date', a.end_date,
                'amount_net', a.amount_net,
                'vat_amount', a.vat_amount,
                'amount_gross', a.amount_gross,
                'currency', a.currency,
                'status', a.status,
                'is_current', a.is_current,
                'identifiers', COALESCE(aib.identifiers, '{}'::jsonb),
                'source_record_id', a.source_record_id
            )
            ORDER BY COALESCE(a.publication_date, a.decision_date, a.submission_date) NULLS LAST
        ) AS acts,
        array_agg(DISTINCT a.id) AS act_ids
    FROM procurement_acts a
    LEFT JOIN act_identifiers_by_act aib ON aib.act_id = a.id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

parties_agg AS (
    SELECT
        a.process_id,
        jsonb_agg(DISTINCT jsonb_build_object(
            'entity_id', e.id,
            'party_role', ap.party_role,
            'name', e.canonical_name,
            'amount', ap.amount,
            'currency', ap.currency,
            'lot_id', ap.lot_id
        )) FILTER (WHERE ap.party_role IN ('SUPPLIER', 'CONTRACTOR', 'CONSORTIUM_MEMBER')) AS suppliers,
        (array_agg(e.id) FILTER (WHERE ap.party_role IN ('BUYER', 'CONTRACTING_AUTHORITY')))[1] AS buyer_entity_id
    FROM procurement_acts a
    JOIN act_parties ap ON ap.act_id = a.id
    JOIN entities e ON e.id = ap.entity_id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

supplier_company_agg AS (
    -- current ΓΕΜΗ snapshot + VIES status per supplier entity, keyed back to process via parties_agg
    SELECT
        pp.id AS process_id,
        jsonb_agg(DISTINCT jsonb_build_object(
            'entity_id', e.id,
            'gemi_number', cs.gemi_number,
            'legal_form', cs.legal_form,
            'company_status', cs.company_status,
            'company_status_observed_at', cs.observed_at,
            'vies_valid', vc.vies_valid,
            'vies_checked_at', vc.checked_at
        )) FILTER (WHERE cs.id IS NOT NULL OR vc.id IS NOT NULL) AS supplier_company_info
    FROM procurement_processes pp
    JOIN procurement_acts a ON a.process_id = pp.id
    JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER', 'CONTRACTOR')
    JOIN entities e ON e.id = ap.entity_id
    LEFT JOIN entity_company_snapshots cs ON cs.entity_id = e.id AND cs.is_current = TRUE
    LEFT JOIN LATERAL (
        SELECT * FROM entity_vies_checks v
        WHERE v.entity_id = e.id
        ORDER BY v.checked_at DESC LIMIT 1
    ) vc ON TRUE
    GROUP BY pp.id
),

lots_agg AS (
    SELECT process_id, jsonb_agg(jsonb_build_object(
        'lot_id', id, 'title', title, 'estimated_value', estimated_value,
        'awarded_value', awarded_value, 'currency', currency, 'status', status
    )) AS lots
    FROM procurement_lots
    GROUP BY process_id
),

documents_agg AS (
    SELECT a.process_id, jsonb_agg(jsonb_build_object(
        'document_id', d.id, 'act_id', dal.act_id,
        'document_type', COALESCE(dal.document_type, d.document_type),
        'title', COALESCE(dal.title, d.title),
        'source_url', COALESCE(dal.source_url, d.source_url),
        'object_uri', d.object_uri,
        'mime_type', d.mime_type, 'file_size', d.file_size,
        'text_extraction_status', d.text_extraction_status, 'page_count', d.page_count,
        'language', d.language
    )) AS documents
    FROM documents d
    JOIN document_act_links dal ON dal.document_id = d.id
    JOIN procurement_acts a ON a.id = dal.act_id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

diavgeia_agg AS (
    -- Διαύγεια decisions are ingested as procurement_acts rows of type
    -- DIAVGEIA_DECISION, joined into the process by the linkage engine.
    SELECT a.process_id, jsonb_agg(jsonb_build_object(
        'act_id', a.id, 'ada', ai.value_normalized, 'title', a.title,
        'decision_date', a.decision_date, 'source_record_id', a.source_record_id
    )) AS diavgeia_decisions
    FROM procurement_acts a
    LEFT JOIN act_identifiers ai ON ai.act_id = a.id AND ai.scheme = 'ADA'
    WHERE a.act_type = 'DIAVGEIA_DECISION' AND a.process_id IS NOT NULL
    GROUP BY a.process_id
),

ted_agg AS (
    SELECT a.process_id, jsonb_agg(jsonb_build_object(
        'act_id', a.id, 'ted_notice_id', t.ted_notice_id, 'eforms_version', t.eforms_version,
        'notice_type', t.notice_type, 'country_code', t.country_code, 'nuts_codes', t.nuts_codes
    )) AS ted_notices
    FROM procurement_acts a
    JOIN ted_notice_details t ON t.act_id = a.id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

funding_agg AS (
    SELECT a.process_id, jsonb_agg(DISTINCT jsonb_build_object(
        'funding_project_id', fp.id, 'mis_ops_code', fp.mis_ops_code,
        'program_period', fp.program_period, 'title', fp.title,
        'budget', fp.budget, 'contracted_amount', fp.contracted_amount,
        'paid_amount', fp.paid_amount,
        'link_method', fl.link_method, 'link_confidence', fl.confidence
    )) AS funding_projects
    FROM procurement_acts a
    JOIN funding_links fl ON fl.act_id = a.id
    JOIN funding_projects fp ON fp.id = fl.funding_project_id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

mef_agg AS (
    SELECT a.process_id, jsonb_agg(jsonb_build_object(
        'mef_expense_id', me.id, 'amount', me.amount, 'expense_date', me.expense_date,
        'link_method', me.link_method, 'confidence', me.confidence
    )) AS mef_expense_signals
    FROM procurement_acts a
    JOIN mef_expenses me ON me.linked_act_id = a.id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

geo_agg AS (
    SELECT a.process_id, jsonb_agg(DISTINCT jsonb_build_object(
        'nuts_code', al.nuts_code, 'municipality_code', al.municipality_code,
        'municipality_name', al.municipality_name,
        'regional_unit_name', al.regional_unit_name,
        'region_name', al.region_name,
        'postal_code', al.postal_code,
        'place_text', al.place_text,
        'location_kind', al.location_kind,
        'granularity', al.granularity,
        'latitude', CASE WHEN al.geom IS NULL THEN NULL ELSE ST_Y(ST_PointOnSurface(al.geom)) END,
        'longitude', CASE WHEN al.geom IS NULL THEN NULL ELSE ST_X(ST_PointOnSurface(al.geom)) END,
        'extraction_method', al.extraction_method,
        'geocode_provider', al.geocode_provider,
        'confidence', al.confidence,
        'evidence', al.evidence,
        'source_record_id', al.source_record_id
    )) AS locations
    FROM procurement_acts a
    JOIN act_locations al ON al.act_id = a.id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

link_confidence_agg AS (
    -- lifecycle_confidence: weakest evidenced connection inside this process's
    -- act graph. NULL (not zero) when a process has a single act and no links yet.
    SELECT aa.process_id, MIN(al.confidence) AS lifecycle_confidence
    FROM acts_agg aa
    JOIN act_links al ON al.from_act_id = ANY (aa.act_ids) OR al.to_act_id = ANY (aa.act_ids)
    GROUP BY aa.process_id
),

freshness_agg AS (
    SELECT a.process_id, MAX(sr.fetched_at) AS freshest_fetch_at
    FROM procurement_acts a
    JOIN source_records sr ON sr.id = a.source_record_id
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
),

open_issues_agg AS (
    SELECT a.process_id, COUNT(*) AS open_issue_count
    FROM procurement_acts a
    JOIN data_quality_issues dqi
        ON dqi.object_type = 'procurement_acts' AND dqi.object_id = a.id AND dqi.status = 'OPEN'
    WHERE a.process_id IS NOT NULL
    GROUP BY a.process_id
)

SELECT
    pp.id                           AS process_id,
    pp.public_id,
    pp.title,
    pp.lifecycle_status,
    pp.record_status,
    pp.merged_into_process_id,
    pp.estimated_value,
    pp.awarded_value,
    pp.current_contract_value,
    pp.currency,
    jsonb_build_object(
        'entity_id', buyer.id,
        'name', buyer.canonical_name,
        'vat', buyer_vat.value_normalized,
        'aaht', buyer_aaht.value_normalized
    )                                AS buyer,
    COALESCE(pa.suppliers, '[]'::jsonb)          AS suppliers,
    COALESCE(sca.supplier_company_info, '[]'::jsonb) AS supplier_company_info,
    COALESCE(aa.acts, '[]'::jsonb)                   AS acts,
    COALESCE(la.lots, '[]'::jsonb)                     AS lots,
    COALESCE(da.documents, '[]'::jsonb)                  AS documents,
    COALESCE(dv.diavgeia_decisions, '[]'::jsonb)           AS diavgeia_decisions,
    COALESCE(te.ted_notices, '[]'::jsonb)                    AS ted_notices,
    COALESCE(fu.funding_projects, '[]'::jsonb)                 AS funding_projects,
    COALESCE(mf.mef_expense_signals, '[]'::jsonb)                AS mef_expense_signals,
    COALESCE(geo.locations, '[]'::jsonb)                           AS locations,
    jsonb_build_object(
        'freshness', fr.freshest_fetch_at,
        'lifecycle_confidence', lc.lifecycle_confidence,
        'open_data_quality_issues', COALESCE(oi.open_issue_count, 0)
    )                                                                 AS data_quality,
    pp.first_observed_at,
    pp.last_observed_at
FROM procurement_processes pp
LEFT JOIN acts_agg aa               ON aa.process_id = pp.id
LEFT JOIN parties_agg pa            ON pa.process_id = pp.id
LEFT JOIN supplier_company_agg sca  ON sca.process_id = pp.id
LEFT JOIN lots_agg la                ON la.process_id = pp.id
LEFT JOIN documents_agg da           ON da.process_id = pp.id
LEFT JOIN diavgeia_agg dv            ON dv.process_id = pp.id
LEFT JOIN ted_agg te                 ON te.process_id = pp.id
LEFT JOIN funding_agg fu             ON fu.process_id = pp.id
LEFT JOIN mef_agg mf                 ON mf.process_id = pp.id
LEFT JOIN geo_agg geo                ON geo.process_id = pp.id
LEFT JOIN link_confidence_agg lc     ON lc.process_id = pp.id
LEFT JOIN freshness_agg fr           ON fr.process_id = pp.id
LEFT JOIN open_issues_agg oi         ON oi.process_id = pp.id
LEFT JOIN entities buyer             ON buyer.id = pa.buyer_entity_id
LEFT JOIN entity_identifiers buyer_vat  ON buyer_vat.entity_id = buyer.id AND buyer_vat.scheme = 'AFM' AND buyer_vat.is_current
LEFT JOIN entity_identifiers buyer_aaht ON buyer_aaht.entity_id = buyer.id AND buyer_aaht.scheme = 'AAHT' AND buyer_aaht.is_current
WHERE pp.record_status = 'ACTIVE';

COMMENT ON VIEW procurement_360 IS
    'Unified per-procurement read model aggregating KHMDHS/Diavgeia/GEMI/ANAPTYXI/TED/VIES/MEF/geo data for one process_id. Source of truth remains the normalized tables in db/migrations/*; this view is the API''s and UI''s primary read path (spec §30.4, §47).';
