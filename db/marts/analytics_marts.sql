-- analytics_marts.sql
-- Market/buyer/supplier analytics implementing the formulas in spec §27, plus
-- the risk/anomaly indicator log (§28) and the opportunity feed (§27.12,
-- §5.1). Built on the canonical tables in db/migrations/*, refreshed via the
-- dependency chain in §37 (contract changed -> supplier profile -> buyer
-- profile -> CPV market -> NUTS market -> funding project), not recomputed
-- wholesale on every write.
--
-- A "market" is CPV (or CPV group) + geography + buyer type + period + procedure
-- type (§5.5). This mart groups by 4-digit CPV prefix and top-level NUTS
-- region per year as the first cut; finer market definitions (buyer type,
-- procedure type slices) are additional dimensions the same pattern extends to.

-- Migration 22 creates the corrected date/CPV-dependent views for upgrades of
-- existing databases. Fresh installs load migrations before this canonical
-- mart file, so replace those views here before defining the complete set.
DROP MATERIALIZED VIEW IF EXISTS renewal_signals;
DROP MATERIALIZED VIEW IF EXISTS cycle_time_metrics;
DROP MATERIALIZED VIEW IF EXISTS payment_execution;
DROP MATERIALIZED VIEW IF EXISTS contract_modification_stats;
DROP MATERIALIZED VIEW IF EXISTS incumbent_signals;
DROP MATERIALIZED VIEW IF EXISTS supplier_dependency;
DROP MATERIALIZED VIEW IF EXISTS buyer_concentration;
DROP MATERIALIZED VIEW IF EXISTS market_hhi;
DROP MATERIALIZED VIEW IF EXISTS supplier_market_share;
DROP MATERIALIZED VIEW IF EXISTS market_value_metrics;

-- ---------------------------------------------------------------------------
-- market_value_metrics: §27.1 (market size) + supporting counts for §27.3
-- (HHI needs supplier_count) and §27.9 (median contract size, §31.6).
-- Default value basis is current_contract_value_net per §27.1's stated
-- default; alternate bases are additional columns, not a config flag, so the
-- UI can show all of them side by side per the §27.3 disclosure requirement.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW market_value_metrics AS
WITH contract_dimensions AS (
    SELECT DISTINCT
        a.id AS contract_act_id,
        a.process_id,
        LEFT(acpv.cpv_code, 4) AS cpv_prefix_4,
        location.nuts_code,
        EXTRACT(YEAR FROM COALESCE(a.decision_date, a.publication_date, a.submission_date, a.start_date))::INT AS period_year,
        a.procedure_type,
        a.amount_net
    FROM procurement_acts a
    JOIN LATERAL (
        SELECT value.cpv_code
        FROM act_cpv_codes value
        WHERE value.act_id = a.id
        ORDER BY value.is_primary DESC, value.cpv_code
        LIMIT 1
    ) acpv ON TRUE
    LEFT JOIN (
        SELECT DISTINCT act_id, nuts_code FROM act_locations WHERE nuts_code IS NOT NULL
    ) location ON location.act_id = a.id
    WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
      AND COALESCE(a.decision_date, a.publication_date, a.submission_date, a.start_date) IS NOT NULL
),
market_totals AS (
    SELECT cpv_prefix_4, nuts_code, period_year, procedure_type,
           COUNT(DISTINCT process_id) AS contract_count,
           SUM(amount_net) AS total_value_net,
           AVG(amount_net) AS avg_value_net,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount_net) AS median_value_net
    FROM contract_dimensions
    GROUP BY cpv_prefix_4, nuts_code, period_year, procedure_type
),
supplier_counts AS (
    SELECT d.cpv_prefix_4, d.nuts_code, d.period_year, d.procedure_type,
           COUNT(DISTINCT p.entity_id) AS supplier_count
    FROM contract_dimensions d
    JOIN act_parties p ON p.act_id = d.contract_act_id AND p.party_role IN ('SUPPLIER','CONTRACTOR')
    GROUP BY d.cpv_prefix_4, d.nuts_code, d.period_year, d.procedure_type
),
buyer_counts AS (
    SELECT d.cpv_prefix_4, d.nuts_code, d.period_year, d.procedure_type,
           COUNT(DISTINCT p.entity_id) AS buyer_count
    FROM contract_dimensions d
    JOIN act_parties p ON p.act_id = d.contract_act_id AND p.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
    GROUP BY d.cpv_prefix_4, d.nuts_code, d.period_year, d.procedure_type
)
SELECT m.*, COALESCE(s.supplier_count, 0) AS supplier_count, COALESCE(b.buyer_count, 0) AS buyer_count
FROM market_totals m
LEFT JOIN supplier_counts s ON s.cpv_prefix_4 = m.cpv_prefix_4
    AND COALESCE(s.nuts_code, '') = COALESCE(m.nuts_code, '')
    AND s.period_year = m.period_year
    AND COALESCE(s.procedure_type, '') = COALESCE(m.procedure_type, '')
LEFT JOIN buyer_counts b ON b.cpv_prefix_4 = m.cpv_prefix_4
    AND COALESCE(b.nuts_code, '') = COALESCE(m.nuts_code, '')
    AND b.period_year = m.period_year
    AND COALESCE(b.procedure_type, '') = COALESCE(m.procedure_type, '');

CREATE UNIQUE INDEX uq_market_value_metrics
    ON market_value_metrics (cpv_prefix_4, COALESCE(nuts_code, ''), period_year, COALESCE(procedure_type, ''));

-- ---------------------------------------------------------------------------
-- supplier_market_share: §27.2. SupplierShare = SupplierContractValue /
-- TotalMarketContractValue, within the same (cpv_prefix_4, nuts_code, year)
-- market key used above.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW supplier_market_share AS
WITH contract_dimensions AS (
    SELECT DISTINCT
        a.id AS contract_act_id, a.process_id, LEFT(acpv.cpv_code, 4) AS cpv_prefix_4,
        location.nuts_code,
        EXTRACT(YEAR FROM COALESCE(a.decision_date, a.publication_date, a.submission_date, a.start_date))::INT AS period_year,
        a.amount_net
    FROM procurement_acts a
    JOIN LATERAL (
        SELECT value.cpv_code
        FROM act_cpv_codes value
        WHERE value.act_id = a.id
        ORDER BY value.is_primary DESC, value.cpv_code
        LIMIT 1
    ) acpv ON TRUE
    LEFT JOIN (
        SELECT DISTINCT act_id, nuts_code FROM act_locations WHERE nuts_code IS NOT NULL
    ) location ON location.act_id = a.id
    WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
      AND COALESCE(a.decision_date, a.publication_date, a.submission_date, a.start_date) IS NOT NULL
),
supplier_allocations AS (
    SELECT d.*, p.entity_id AS supplier_entity_id,
           CASE
               WHEN d.amount_net IS NULL THEN p.amount
               WHEN SUM(COALESCE(p.amount, 0)) OVER market_contract > 0
                   THEN d.amount_net * COALESCE(p.amount, 0) / SUM(COALESCE(p.amount, 0)) OVER market_contract
               ELSE d.amount_net / NULLIF(COUNT(*) OVER market_contract, 0)
           END AS allocated_value
    FROM contract_dimensions d
    JOIN act_parties p ON p.act_id = d.contract_act_id AND p.party_role IN ('SUPPLIER','CONTRACTOR')
    WINDOW market_contract AS (
        PARTITION BY d.contract_act_id, d.cpv_prefix_4, COALESCE(d.nuts_code, ''), d.period_year
    )
)
SELECT a.cpv_prefix_4, a.nuts_code, a.period_year, a.supplier_entity_id,
       SUM(a.allocated_value) AS supplier_value,
       COUNT(DISTINCT a.process_id) AS supplier_contract_count,
       COUNT(DISTINCT buyer.entity_id) AS supplier_buyer_count
FROM supplier_allocations a
LEFT JOIN act_parties buyer ON buyer.act_id = a.contract_act_id
    AND buyer.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
GROUP BY a.cpv_prefix_4, a.nuts_code, a.period_year, a.supplier_entity_id;

CREATE INDEX ix_supplier_market_share_key
    ON supplier_market_share (cpv_prefix_4, COALESCE(nuts_code, ''), period_year);

-- ---------------------------------------------------------------------------
-- market_hhi: §27.3. HHI = Σ share_i^2, shares in percentage points (0-10000).
-- UI must disclose period, market, supplier_count and value-vs-count basis
-- (enforced at the API layer; supplier_count is carried here for that).
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW market_hhi AS
SELECT
    sms.cpv_prefix_4,
    sms.nuts_code,
    sms.period_year,
    COUNT(*) AS supplier_count,
    SUM(POWER(100.0 * sms.supplier_value / NULLIF(mkt.total_value_net, 0), 2)) AS hhi
FROM supplier_market_share sms
JOIN market_value_metrics mkt
    ON mkt.cpv_prefix_4 = sms.cpv_prefix_4
   AND COALESCE(mkt.nuts_code,'') = COALESCE(sms.nuts_code,'')
   AND mkt.period_year = sms.period_year
GROUP BY sms.cpv_prefix_4, sms.nuts_code, sms.period_year;

-- ---------------------------------------------------------------------------
-- buyer_concentration: §27.4. TopSupplierShare = value awarded by buyer to
-- top supplier / total buyer awarded value. Framed as a commercial
-- concentration indicator, not evidence of favoritism (§27.4, §41.3).
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW buyer_concentration AS
WITH buyer_supplier_value AS (
    SELECT buyer_ap.entity_id AS buyer_entity_id, ap.entity_id AS supplier_entity_id,
           SUM(ap.amount) AS value
    FROM procurement_acts a
    JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER','CONTRACTOR')
    JOIN act_parties buyer_ap ON buyer_ap.act_id = a.id AND buyer_ap.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
    WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
    GROUP BY buyer_ap.entity_id, ap.entity_id
),
buyer_totals AS (
    SELECT buyer_entity_id, SUM(value) AS total_value
    FROM buyer_supplier_value GROUP BY buyer_entity_id
),
top_supplier AS (
    SELECT DISTINCT ON (buyer_entity_id) buyer_entity_id, supplier_entity_id, value AS top_value
    FROM buyer_supplier_value
    ORDER BY buyer_entity_id, value DESC
)
SELECT
    bt.buyer_entity_id,
    ts.supplier_entity_id AS top_supplier_entity_id,
    ts.top_value,
    bt.total_value,
    ts.top_value / NULLIF(bt.total_value, 0) AS top_supplier_share
FROM buyer_totals bt
JOIN top_supplier ts ON ts.buyer_entity_id = bt.buyer_entity_id;

-- ---------------------------------------------------------------------------
-- supplier_dependency: §27.5. SupplierBuyerDependency = supplier value from
-- buyer X / supplier total *recorded* public-sector value. UI copy must use
-- "recorded" (καταγεγραμμένης) — the platform does not know private revenue.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW supplier_dependency AS
WITH supplier_buyer_value AS (
    SELECT ap.entity_id AS supplier_entity_id, buyer_ap.entity_id AS buyer_entity_id,
           SUM(ap.amount) AS value
    FROM procurement_acts a
    JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER','CONTRACTOR')
    JOIN act_parties buyer_ap ON buyer_ap.act_id = a.id AND buyer_ap.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
    WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
    GROUP BY ap.entity_id, buyer_ap.entity_id
),
supplier_totals AS (
    SELECT supplier_entity_id, SUM(value) AS total_recorded_value
    FROM supplier_buyer_value GROUP BY supplier_entity_id
)
SELECT
    sbv.supplier_entity_id,
    sbv.buyer_entity_id,
    sbv.value AS value_from_buyer,
    st.total_recorded_value,
    sbv.value / NULLIF(st.total_recorded_value, 0) AS dependency_ratio
FROM supplier_buyer_value sbv
JOIN supplier_totals st ON st.supplier_entity_id = sbv.supplier_entity_id;

-- ---------------------------------------------------------------------------
-- incumbent_signals: §27.6. Heuristic, not a prediction — "likely incumbent"
-- with explicit evidence, never "the certain winner".
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW incumbent_signals AS
SELECT DISTINCT ON (buyer_ap.entity_id, cpv.prefix_4)
    buyer_ap.entity_id AS buyer_entity_id,
    cpv.prefix_4 AS cpv_prefix_4,
    ap.entity_id AS incumbent_supplier_entity_id,
    a.id AS most_recent_active_contract_act_id,
    a.end_date,
    0.91::NUMERIC(5,4) AS confidence   -- baseline per §27.6 worked example; recalibrate once labeled outcomes exist
FROM procurement_acts a
JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER','CONTRACTOR')
JOIN act_parties buyer_ap ON buyer_ap.act_id = a.id AND buyer_ap.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
JOIN act_cpv_codes acpv ON acpv.act_id = a.id AND acpv.is_primary = TRUE
JOIN cpv_codes cpv ON cpv.code = acpv.cpv_code
WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
  AND (a.end_date IS NULL OR a.end_date >= CURRENT_DATE)
  AND NOT EXISTS (
      SELECT 1 FROM act_links al
      JOIN procurement_acts cancel_act ON cancel_act.id = al.from_act_id AND cancel_act.act_type = 'CANCELLATION'
      WHERE al.to_act_id = a.id AND al.link_type = 'CANCELS'
  )
ORDER BY buyer_ap.entity_id, cpv.prefix_4,
         COALESCE(a.decision_date, a.publication_date, a.submission_date, a.start_date) DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- contract_modification_stats: §27.7-27.8. ModificationRate and ValueUplift,
-- computed only over AMENDS links (i.e. confirmed amendments of the same
-- contract, not independent contracts).
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW contract_modification_stats AS
WITH amendment_counts AS (
    SELECT to_act_id AS contract_act_id, COUNT(*) AS amendment_count
    FROM act_links WHERE link_type = 'AMENDS'
    GROUP BY to_act_id
),
value_uplift AS (
    SELECT
        c.id AS contract_act_id,
        c.amount_net AS original_value,
        c.amount_net + COALESCE(SUM(amend.amount_net), 0) AS current_value
    FROM procurement_acts c
    LEFT JOIN act_links al ON al.to_act_id = c.id AND al.link_type = 'AMENDS'
    LEFT JOIN procurement_acts amend ON amend.id = al.from_act_id
    WHERE c.act_type = 'CONTRACT'
    GROUP BY c.id, c.amount_net
)
SELECT
    vu.contract_act_id,
    vu.original_value,
    vu.current_value,
    COALESCE(ac.amendment_count, 0) AS amendment_count,
    (vu.current_value - vu.original_value) / NULLIF(vu.original_value, 0) AS value_uplift_ratio
FROM value_uplift vu
LEFT JOIN amendment_counts ac ON ac.contract_act_id = vu.contract_act_id;

-- ---------------------------------------------------------------------------
-- cycle_time_metrics: §27.9. Only computed over act_links with confidence >=
-- 0.95 (the auto-link threshold, §8.3) so weak/fuzzy links don't pollute
-- timing statistics.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW cycle_time_metrics AS
SELECT
    request.process_id,
    (COALESCE(notice.publication_date, notice.submission_date, notice.decision_date, notice.start_date)
        - COALESCE(request.submission_date, request.publication_date, request.decision_date, request.start_date)) AS request_to_notice_days,
    (COALESCE(award.decision_date, award.publication_date, award.submission_date, award.start_date)
        - COALESCE(notice.publication_date, notice.submission_date, notice.decision_date, notice.start_date)) AS notice_to_award_days,
    (COALESCE(contract.decision_date, contract.publication_date, contract.submission_date, contract.start_date)
        - COALESCE(award.decision_date, award.publication_date, award.submission_date, award.start_date)) AS award_to_contract_days,
    (COALESCE(first_payment.decision_date, first_payment.publication_date, first_payment.submission_date, first_payment.start_date)
        - COALESCE(contract.decision_date, contract.publication_date, contract.submission_date, contract.start_date)) AS contract_to_first_payment_days
FROM procurement_acts request
LEFT JOIN act_links l1 ON l1.from_act_id = request.id AND l1.link_type = 'ANNOUNCES' AND l1.confidence >= 0.95
LEFT JOIN procurement_acts notice ON notice.id = l1.to_act_id
LEFT JOIN act_links l2 ON l2.to_act_id = notice.id AND l2.link_type = 'AWARDS' AND l2.confidence >= 0.95
LEFT JOIN procurement_acts award ON award.id = l2.from_act_id
LEFT JOIN act_links l3 ON l3.from_act_id = award.id AND l3.link_type = 'EXECUTES' AND l3.confidence >= 0.95
LEFT JOIN procurement_acts contract ON contract.id = l3.to_act_id
LEFT JOIN act_links l4 ON l4.to_act_id = contract.id AND l4.link_type = 'PAYS' AND l4.confidence >= 0.95
LEFT JOIN procurement_acts first_payment ON first_payment.id = l4.from_act_id
WHERE request.act_type = 'REQUEST';

-- ---------------------------------------------------------------------------
-- payment_execution: §27.10. Coverage badge distinguishes payment order vs
-- declared expense vs confirmed cash payment, and must always be shown.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW payment_execution AS
SELECT
    c.id AS contract_act_id,
    c.amount_net AS current_contract_value,
    COALESCE(SUM(pay.amount_net), 0) AS linked_payment_amount,
    COALESCE(SUM(pay.amount_net), 0) / NULLIF(c.amount_net, 0) AS payment_execution_ratio,
    CASE
        WHEN COUNT(pay.id) = 0 THEN 'UNKNOWN'
        WHEN COALESCE(SUM(pay.amount_net),0) / NULLIF(c.amount_net,0) >= 0.9 THEN 'HIGH_COVERAGE'
        WHEN COALESCE(SUM(pay.amount_net),0) / NULLIF(c.amount_net,0) >= 0.3 THEN 'PARTIAL_COVERAGE'
        ELSE 'SPECIALIZED_SOURCE_ONLY'
    END AS coverage_badge
FROM procurement_acts c
LEFT JOIN act_links al ON al.to_act_id = c.id AND al.link_type = 'PAYS'
LEFT JOIN procurement_acts pay ON pay.id = al.from_act_id AND pay.act_type = 'PAYMENT'
WHERE c.act_type = 'CONTRACT'
GROUP BY c.id, c.amount_net;

-- ---------------------------------------------------------------------------
-- renewal_signals: §27.11. Rule-based first version combining remaining days
-- with the buyer's average lead time.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW renewal_signals AS
WITH buyer_lead_time AS (
    SELECT process_id_buyer.buyer_entity_id, AVG(ctm.notice_to_award_days + ctm.award_to_contract_days) AS avg_lead_time_days
    FROM cycle_time_metrics ctm
    JOIN procurement_processes process_id_buyer ON process_id_buyer.id = ctm.process_id
    WHERE ctm.notice_to_award_days IS NOT NULL
    GROUP BY process_id_buyer.buyer_entity_id
)
SELECT
    c.id AS contract_act_id,
    c.process_id,
    c.end_date,
    (c.end_date - CURRENT_DATE) AS days_to_end,
    blt.avg_lead_time_days,
    (c.end_date - CURRENT_DATE) <= COALESCE(blt.avg_lead_time_days, 90) AS renewal_watch_active
FROM procurement_acts c
JOIN procurement_processes pp ON pp.id = c.process_id
LEFT JOIN buyer_lead_time blt ON blt.buyer_entity_id = pp.buyer_entity_id
WHERE c.act_type = 'CONTRACT' AND c.is_current = TRUE AND c.end_date IS NOT NULL;

-- ---------------------------------------------------------------------------
-- opportunity_scores: §5.1, §27.12. Computed and upserted by
-- services/analytics (weights below), not a pure SQL view — "competitive
-- attractiveness" and "timing" draw on heuristics/future ML that belong in
-- application code. Table shape carries the required explainability: every
-- sub-score plus its evidence bullet points.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opportunity_scores (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id                UUID NOT NULL REFERENCES procurement_processes(id),
    tenant_id                    UUID NOT NULL REFERENCES tenants(id),   -- score is company-fit-relative, so tenant-scoped
    profile_version                 INTEGER NOT NULL DEFAULT 1,
    total_score                    NUMERIC(5,2) NOT NULL,                  -- 0-100
    cpv_company_fit_score              NUMERIC(5,2) NOT NULL,                -- 25% weight
    buyer_affinity_score                  NUMERIC(5,2) NOT NULL,               -- 20% weight
    timing_score                            NUMERIC(5,2) NOT NULL,              -- 15% weight
    competitive_attractiveness_score          NUMERIC(5,2) NOT NULL,             -- 15% weight
    contract_value_fit_score                    NUMERIC(5,2) NOT NULL,            -- 15% weight
    data_confidence_score                         NUMERIC(5,2) NOT NULL,           -- 10% weight
    evidence                                        JSONB NOT NULL,                  -- list of + / - bullet explanations
    computed_at                                       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (process_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_opportunity_scores_tenant
    ON opportunity_scores (tenant_id, total_score DESC);
CREATE INDEX IF NOT EXISTS ix_opportunity_scores_tenant_profile
    ON opportunity_scores (tenant_id, profile_version, total_score DESC);

COMMENT ON TABLE opportunity_scores IS
    'Never rename/expose as "win probability" until reliable participation/outcome labels exist (§27.12).';

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'procintel_app') THEN
        ALTER TABLE opportunity_scores ENABLE ROW LEVEL SECURITY;
        ALTER TABLE opportunity_scores FORCE ROW LEVEL SECURITY;
        IF NOT EXISTS (
            SELECT 1
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = 'opportunity_scores'
              AND policyname = 'tenant_isolation'
        ) THEN
            CREATE POLICY tenant_isolation ON opportunity_scores
                USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
        END IF;
    END IF;
END
$$;
