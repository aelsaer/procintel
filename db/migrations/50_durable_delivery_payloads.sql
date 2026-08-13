-- Persist the exact signed webhook request so every retry is byte-identical.

ALTER TABLE webhook_deliveries
    ADD COLUMN IF NOT EXISTS request_body TEXT;

ALTER TABLE webhook_deliveries
    ADD COLUMN IF NOT EXISTS last_error JSONB;

UPDATE saas_plans
SET entitlements = entitlements || CASE code
    WHEN 'STARTER' THEN '{"provider_fetches_month":50}'::jsonb
    WHEN 'PROFESSIONAL' THEN '{"provider_fetches_month":500}'::jsonb
    WHEN 'ENTERPRISE' THEN '{"provider_fetches_month":-1}'::jsonb
    ELSE '{}'::jsonb
END
WHERE code IN ('STARTER', 'PROFESSIONAL', 'ENTERPRISE');
