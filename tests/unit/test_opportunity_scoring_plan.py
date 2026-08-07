import inspect
from types import SimpleNamespace

from services.analytics.opportunity_scoring import _load_candidates, candidate_taxonomy_scope


def test_scoring_selects_recent_candidate_processes_before_enrichment() -> None:
    source = inspect.getsource(_load_candidates)

    assert "candidate_processes AS MATERIALIZED" in source
    assert "opportunity.act_type IN ('REQUEST', 'APPROVED_REQUEST', 'NOTICE')" in source
    assert "LEFT JOIN LATERAL" in source
    assert "procintel_act_is_analytics_eligible(opportunity.id)" in source
    assert "procintel_taxonomy_match" in source


def test_candidate_taxonomy_scope_preserves_keyword_required_semantics() -> None:
    cpv_likes, keyword_patterns = candidate_taxonomy_scope([
        SimpleNamespace(filters={
            "cpv_prefixes": ["72"],
            "keywords": ["GIS"],
            "taxonomy_match_mode": "KEYWORD_REQUIRED",
        })
    ])

    assert cpv_likes == []
    assert keyword_patterns


def test_candidate_taxonomy_scope_disables_prefilter_for_unscoped_rule() -> None:
    cpv_likes, keyword_patterns = candidate_taxonomy_scope([
        SimpleNamespace(filters={"cpv_prefixes": ["72"]}),
        SimpleNamespace(filters={}),
    ])

    assert cpv_likes == []
    assert keyword_patterns == []
