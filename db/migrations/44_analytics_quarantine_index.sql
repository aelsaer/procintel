-- Keep the analytics eligibility predicate cheap even when data-quality
-- checks have recorded hundreds of thousands of informational findings.
-- Only unresolved act-level ERROR/BLOCKING rows can quarantine an act.

CREATE INDEX IF NOT EXISTS ix_dq_analytics_quarantine
    ON data_quality_issues (object_id)
    WHERE object_id IS NOT NULL
      AND LOWER(COALESCE(object_type, '')) IN (
          'procurement_act',
          'procurement_acts'
      )
      AND severity IN ('ERROR', 'BLOCKING')
      AND status <> 'RESOLVED';
