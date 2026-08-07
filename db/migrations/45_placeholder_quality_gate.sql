-- adamChain rows preserve lifecycle identifiers before their corresponding
-- resource is fetched. They are evidence, not procurement facts, and must not
-- enter product metrics or search results while they have no substantive data.

CREATE OR REPLACE FUNCTION procintel_act_is_analytics_eligible(target_act_id UUID)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
PARALLEL SAFE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM procurement_acts act
        JOIN source_records source ON source.id = act.source_record_id
        WHERE act.id = target_act_id
          AND act.is_current = TRUE
          AND NOT (
              source.source_system = 'KHMDHS'
              AND source.resource_type = 'adamChain'
              AND act.title IS NULL
              AND act.amount_net IS NULL
              AND act.amount_gross IS NULL
              AND act.publication_date IS NULL
              AND act.submission_date IS NULL
              AND act.decision_date IS NULL
              AND act.start_date IS NULL
              AND act.end_date IS NULL
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM data_quality_issues issue
        WHERE issue.object_id = target_act_id
          AND LOWER(COALESCE(issue.object_type, '')) IN (
              'procurement_act',
              'procurement_acts'
          )
          AND issue.severity IN ('ERROR', 'BLOCKING')
          AND issue.status <> 'RESOLVED'
    )
$$;

COMMENT ON FUNCTION procintel_act_is_analytics_eligible(UUID) IS
    'False for unresolved ERROR/BLOCKING issues and empty adamChain evidence placeholders.';
