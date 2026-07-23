-- 15_alert_delivery_targets.sql
-- Concrete per-rule delivery destinations. alert_rules.delivery_channels
-- names channel *types* (§32); this table supplies the actual address a
-- real EMAIL/WEBHOOK/TEAMS/SLACK channel implementation
-- (services/alerts/delivery.py) sends to, plus a per-target signing secret
-- for the §30.5 webhook envelope's "signature" requirement.

CREATE TABLE alert_delivery_targets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_rule_id   UUID NOT NULL REFERENCES alert_rules(id),
    channel_type    TEXT NOT NULL,   -- EMAIL | WEBHOOK | TEAMS | SLACK
    target          TEXT NOT NULL,   -- email address, or webhook/Teams/Slack incoming URL
    secret          TEXT,            -- HMAC signing secret (WEBHOOK/TEAMS/SLACK); NULL for EMAIL
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_alert_delivery_targets_rule
    ON alert_delivery_targets (alert_rule_id, channel_type) WHERE is_active;
