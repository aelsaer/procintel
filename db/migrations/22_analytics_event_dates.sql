-- 22_analytics_event_dates.sql
-- Use the source-native event date fallback used by KHMDHS and aggregate each
-- contract once per market dimension. Earlier marts required decision_date and
-- multiplied values through party/location joins.

DROP MATERIALIZED VIEW IF EXISTS market_hhi;
DROP MATERIALIZED VIEW IF EXISTS supplier_market_share;
DROP MATERIALIZED VIEW IF EXISTS market_value_metrics;

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

DROP MATERIALIZED VIEW IF EXISTS renewal_signals;
DROP MATERIALIZED VIEW IF EXISTS cycle_time_metrics;

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

CREATE MATERIALIZED VIEW renewal_signals AS
WITH buyer_lead_time AS (
    SELECT process.buyer_entity_id,
           AVG(ctm.notice_to_award_days + ctm.award_to_contract_days) AS avg_lead_time_days
    FROM cycle_time_metrics ctm
    JOIN procurement_processes process ON process.id = ctm.process_id
    WHERE ctm.notice_to_award_days IS NOT NULL
    GROUP BY process.buyer_entity_id
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
