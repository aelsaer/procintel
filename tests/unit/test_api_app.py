"""DB-free checks: the app must be importable and its schema/routes buildable
without DATABASE_URL set (apps/api/db.py creates its engine lazily), and the
one DB-free endpoint (/health) must actually work end-to-end."""

import httpx

from apps.api.main import app

EXPECTED_PATHS = {
    "/health",
    "/v1/contracts/{identifier}",
    "/v1/processes/{process_id}",
    "/v1/processes/{process_id}/timeline",
    "/v1/search",
    "/v1/buyers/{buyer_id}",
    "/v1/buyers/{buyer_id}/suppliers",
    "/v1/companies/{company_id}",
    "/v1/companies/{company_id}/contracts",
    "/v1/alert-rules",
    "/v1/search/fulltext",
    "/v1/fetch-requests",
    "/v1/fetch-requests/{request_id}",
    "/v1/analytics/top-suppliers",
    "/v1/analytics/top-buyers",
    "/v1/analytics/market-overview",
    "/v1/analytics/opportunities",
    "/v1/analytics/region-activity",
    "/v1/analytics/regions",
    "/v1/analytics/locations",
    "/v1/analytics/data-coverage",
    "/v1/competitors/discover",
    "/v1/competitors/{company_id}",
    "/v1/processes/{process_id}/competition",
    "/v1/processes/{process_id}/similar-contracts",
    "/v1/alert-rules/{rule_id}",
    "/v1/alert-rules/{rule_id}/preview",
    "/v1/alert-rules/events",
    "/v1/alert-rules/events/{event_id}/read",
    "/v1/alert-rules/delivery-history",
    "/v1/alert-rules/digest-history",
    "/v1/business-profile",
    "/v1/business-profile/classify",
    "/v1/business-profile/scoring-status",
    "/v1/workspace/me",
    "/v1/workspace/login",
    "/v1/workspace/saved-searches",
    "/v1/workspace/saved-searches/{item_id}",
    "/v1/workspace/pipeline",
    "/v1/workspace/pipeline/{item_id}",
    "/v1/workspace/notes",
    "/v1/workspace/notes/{note_id}",
    "/v1/workspace/tags",
    "/v1/workspace/tags/links",
    "/v1/workspace/tags/{tag_id}/links",
    "/v1/workspace/tags/{tag_id}/links/{object_type}/{object_id}",
    "/v1/workspace/watches",
    "/v1/workspace/watches/{watch_id}",
    "/v1/evidence/methodologies",
    "/v1/evidence/methodologies/{metric}",
    "/v1/evidence/{object_type}/{object_id}",
    "/v1/entity-review/candidates",
    "/v1/entity-review/candidates/{candidate_id}/review",
    "/v1/entity-review/generate",
    "/v1/entity-review/merges",
    "/v1/entity-review/merges/{merge_id}/undo",
    "/v1/intelligence/markets",
    "/v1/intelligence/market-dashboard",
    "/v1/intelligence/opportunities",
    "/v1/intelligence/buyers/{buyer_id}",
    "/v1/intelligence/suppliers/{supplier_id}",
    "/v1/intelligence/renewals",
    "/v1/intelligence/risk-indicators",
    "/v1/intelligence/funding",
    "/v1/intelligence/relationships",
    "/v1/intelligence/assistant",
    "/v1/exports",
    "/v1/exports/{job_id}/download",
    "/v1/exports/{job_id}/retry",
}


def test_openapi_schema_builds_without_a_database():
    schema = app.openapi()
    assert set(schema["paths"].keys()) == EXPECTED_PATHS


async def test_health_endpoint_works_without_a_database():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
