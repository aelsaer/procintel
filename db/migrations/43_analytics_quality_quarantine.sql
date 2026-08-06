-- Analytics must never present known-invalid canonical acts as trustworthy
-- market intelligence. Keep quarantined records searchable for diagnosis and
-- provenance, but exclude them from metrics until their issue is resolved.

CREATE OR REPLACE FUNCTION procintel_act_is_analytics_eligible(target_act_id UUID)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
PARALLEL SAFE
AS $$
    SELECT NOT EXISTS (
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
    'False while an act has any unresolved ERROR/BLOCKING quality issue; '
    'used to quarantine bad data from analytics without deleting evidence.';
