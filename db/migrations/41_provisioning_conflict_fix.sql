-- Remove PL/pgSQL ambiguity between the function's tenant_id output column
-- and tenant_subscriptions.tenant_id in its conflict target.
CREATE OR REPLACE FUNCTION procintel_provision_tenant(
    p_issuer TEXT,
    p_subject TEXT,
    p_email TEXT,
    p_organization_name TEXT
)
RETURNS TABLE (tenant_id UUID, user_id UUID, created BOOLEAN)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    bound_tenant UUID;
    bound_user UUID;
    existing_tenant UUID;
    selected_plan TEXT := 'STARTER';
    trial_days INTEGER := 14;
BEGIN
    IF length(trim(p_issuer)) = 0 OR length(trim(p_subject)) = 0 THEN
        RAISE EXCEPTION 'issuer and subject are required';
    END IF;
    IF p_email IS NULL OR p_email !~* '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN
        RAISE EXCEPTION 'a valid verified email is required';
    END IF;

    SELECT b.tenant_id, b.user_id
      INTO bound_tenant, bound_user
      FROM oidc_subject_tenant_bindings b
     WHERE b.issuer = p_issuer AND b.subject = p_subject;
    IF bound_tenant IS NOT NULL THEN
        UPDATE oidc_subject_tenant_bindings
           SET last_login_at = now()
         WHERE issuer = p_issuer AND subject = p_subject;
        RETURN QUERY SELECT bound_tenant, bound_user, FALSE;
        RETURN;
    END IF;

    INSERT INTO users (id, email, display_name)
    VALUES (gen_random_uuid(), lower(trim(p_email)), split_part(p_email, '@', 1))
    ON CONFLICT (email) DO UPDATE SET
        display_name = COALESCE(users.display_name, EXCLUDED.display_name)
    RETURNING id INTO bound_user;

    SELECT tm.tenant_id
      INTO existing_tenant
      FROM tenant_memberships tm
     WHERE tm.user_id = bound_user
     ORDER BY tm.created_at
     LIMIT 1;

    IF existing_tenant IS NULL THEN
        INSERT INTO tenants (id, name, plan)
        VALUES (
            gen_random_uuid(),
            COALESCE(NULLIF(trim(p_organization_name), ''), split_part(p_email, '@', 2)),
            selected_plan
        )
        RETURNING id INTO bound_tenant;

        INSERT INTO tenant_memberships (id, tenant_id, user_id, role)
        VALUES (gen_random_uuid(), bound_tenant, bound_user, 'OWNER');

        SELECT COALESCE(sp.trial_days, 14)
          INTO trial_days
          FROM saas_plans sp
         WHERE sp.code = selected_plan;

        INSERT INTO tenant_subscriptions (
            id, tenant_id, plan_code, status, billing_provider,
            trial_started_at, trial_ends_at, current_period_start, current_period_end
        )
        VALUES (
            gen_random_uuid(), bound_tenant, selected_plan, 'TRIALING', 'MANUAL',
            now(), now() + make_interval(days => trial_days),
            now(), now() + make_interval(days => trial_days)
        )
        ON CONFLICT ON CONSTRAINT tenant_subscriptions_tenant_id_key DO NOTHING;

        INSERT INTO account_success_tasks (id, tenant_id, title, description, due_at)
        VALUES
            (gen_random_uuid(), bound_tenant, 'Confirm company profile', 'Review CPV and keyword suggestions.', now() + interval '1 day'),
            (gen_random_uuid(), bound_tenant, 'Review first opportunity shortlist', 'Validate the first ten opportunities.', now() + interval '2 days'),
            (gen_random_uuid(), bound_tenant, 'Configure daily alerts', 'Confirm recipients and digest time.', now() + interval '3 days'),
            (gen_random_uuid(), bound_tenant, 'Book managed profile review', 'A Procintel analyst reviews market coverage.', now() + interval '5 days');
        created := TRUE;
    ELSE
        bound_tenant := existing_tenant;
        created := FALSE;
    END IF;

    INSERT INTO oidc_subject_tenant_bindings (
        id, issuer, subject, tenant_id, user_id, last_login_at
    )
    VALUES (
        gen_random_uuid(), p_issuer, p_subject, bound_tenant, bound_user, now()
    )
    ON CONFLICT (issuer, subject) DO UPDATE SET
        last_login_at = now();

    RETURN QUERY SELECT bound_tenant, bound_user, created;
END;
$$;

REVOKE ALL ON FUNCTION procintel_provision_tenant(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION procintel_provision_tenant(TEXT, TEXT, TEXT, TEXT) TO procintel_app;
