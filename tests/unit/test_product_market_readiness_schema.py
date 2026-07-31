from pathlib import Path


MIGRATION = (
    Path(__file__).parents[2]
    / "db"
    / "migrations"
    / "37_product_market_readiness.sql"
)


def test_market_readiness_migration_covers_every_product_workflow():
    sql = MIGRATION.read_text(encoding="utf-8")
    required_tables = {
        "onboarding_sessions",
        "profile_review_requests",
        "source_completeness_snapshots",
        "decision_makers",
        "decision_maker_watches",
        "framework_supplier_memberships",
        "framework_watches",
        "tenant_bid_content",
        "proposal_sections",
        "proposal_section_versions",
        "saas_plans",
        "tenant_subscriptions",
        "entitlement_usage",
        "support_tickets",
        "sector_profile_templates",
        "document_phrase_monitors",
        "document_transformation_jobs",
        "eu_benchmark_snapshots",
        "tenant_cross_border_matches",
    }
    for table in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_every_tenant_owned_market_readiness_table_has_rls():
    sql = MIGRATION.read_text(encoding="utf-8")
    rls_block = sql[sql.index("FOREACH table_name IN ARRAY ARRAY[") :]
    for table in (
        "onboarding_sessions",
        "profile_review_requests",
        "decision_maker_watches",
        "framework_watches",
        "tenant_bid_content",
        "proposal_sections",
        "proposal_section_versions",
        "proposal_exports",
        "tenant_subscriptions",
        "entitlement_usage",
        "support_tickets",
        "document_phrase_monitors",
        "document_phrase_matches",
        "document_transformation_jobs",
        "tenant_cross_border_matches",
    ):
        assert f"'{table}'" in rls_block


def test_public_plans_and_sector_templates_are_seeded():
    sql = MIGRATION.read_text(encoding="utf-8")
    for plan in ("STARTER", "PROFESSIONAL", "ENTERPRISE"):
        assert f"('{plan}'" in sql
    for template in ("ICT", "ENVIRONMENT", "CONSTRUCTION", "HEALTH", "CONSULTING"):
        assert f"('{template}'" in sql


def test_cross_border_migrations_reference_ted_acts_and_handle_constraint_names():
    migrations = Path(__file__).parents[2] / "db" / "migrations"
    european = (migrations / "39_european_intelligence.sql").read_text(encoding="utf-8")
    cleanup = (migrations / "40_cross_border_constraint_cleanup.sql").read_text(encoding="utf-8")

    assert "act_id UUID REFERENCES procurement_acts(id)" in european
    assert "uq_cross_border_match_act" in european
    assert "pg_get_constraintdef" in european
    assert "pg_get_constraintdef" in cleanup
