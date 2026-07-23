-- 09_alerts.sql
-- Alert rules, deduplicated event log, and webhook delivery tracking.
-- Spec refs: description.txt §5.8, §30.5, §32.

CREATE TABLE alert_rules (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenants(id),
    user_id                 UUID NOT NULL REFERENCES users(id),
    name                       TEXT NOT NULL,
    event_types                  TEXT[] NOT NULL,
        -- opportunity.created | opportunity.updated | contract.created |
        -- contract.modified | contract.expiring | payment.detected |
        -- company.status_changed | buyer.new_procurement (§30.5)
    filters                        JSONB NOT NULL,           -- cpv, amount thresholds, nuts/municipality, buyer/supplier ids, ...
    schedule                          TEXT NOT NULL,           -- IMMEDIATE | DAILY_DIGEST
    delivery_channels                    TEXT[] NOT NULL,       -- EMAIL | IN_APP | WEBHOOK | TEAMS | SLACK
    is_active                              BOOLEAN NOT NULL DEFAULT TRUE,
    last_evaluated_at                        TIMESTAMPTZ,
    created_at                                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_alert_rules_active ON alert_rules (is_active) WHERE is_active = TRUE;

-- ---------------------------------------------------------------------------
-- alert_events: the deduplicated firing log. dedup key = alert_rule_id +
-- canonical_object_id + event_type + material_change_hash (§32.2) — a
-- re-ingestion that changes only the fetch timestamp produces the same
-- material_change_hash and therefore no duplicate row/notification (§32.3).
-- ---------------------------------------------------------------------------
CREATE TABLE alert_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_rule_id             UUID NOT NULL REFERENCES alert_rules(id),
    canonical_object_type        TEXT NOT NULL,
    canonical_object_id            UUID NOT NULL,
    event_type                       TEXT NOT NULL,
    material_change_hash               TEXT NOT NULL,
    payload                               JSONB NOT NULL,
    triggered_at                           TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at                             TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_alert_event_dedup
    ON alert_events (alert_rule_id, canonical_object_id, event_type, material_change_hash);

-- ---------------------------------------------------------------------------
-- webhook_deliveries (§30.5): each webhook carries event id, idempotency
-- key, timestamp, tenant id, retry policy, signature.
-- ---------------------------------------------------------------------------
CREATE TABLE webhook_deliveries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_event_id         UUID NOT NULL REFERENCES alert_events(id),
    tenant_id                 UUID NOT NULL REFERENCES tenants(id),
    endpoint_url                 TEXT NOT NULL,
    idempotency_key                 TEXT NOT NULL,
    signature                         TEXT NOT NULL,
    status                               TEXT NOT NULL DEFAULT 'PENDING', -- PENDING | DELIVERED | FAILED
    attempt_count                          INTEGER NOT NULL DEFAULT 0,
    last_attempt_at                          TIMESTAMPTZ,
    next_retry_at                              TIMESTAMPTZ,
    response_status                              INTEGER,
    created_at                                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX ix_webhook_deliveries_pending
    ON webhook_deliveries (status, next_retry_at) WHERE status = 'PENDING';
