"""FastAPI app — description.txt §30.1 (subset implemented so far).

    uvicorn apps.api.main:app --reload

Requires DATABASE_URL (see infra/docker/.env.example); importing this
module does not, so tests can build the OpenAPI schema without a DB.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import (
    analytics,
    alert_rules,
    business_profiles,
    buyers,
    companies,
    competitors,
    contracts,
    entity_review,
    evidence,
    exports,
    fetch_requests,
    intelligence,
    processes,
    search,
    search_fulltext,
    workspace,
)

app = FastAPI(
    title="Procurement Intelligence API",
    version="0.1.0",
    description="Greek Public Procurement Intelligence platform with cross-source procurement enrichment.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(contracts.router)
app.include_router(processes.router)
app.include_router(search.router)
app.include_router(search_fulltext.router)
app.include_router(fetch_requests.router)
app.include_router(buyers.router)
app.include_router(companies.router)
app.include_router(alert_rules.router)
app.include_router(business_profiles.router)
app.include_router(workspace.router)
app.include_router(evidence.router)
app.include_router(entity_review.router)
app.include_router(intelligence.router)
app.include_router(exports.router)
app.include_router(analytics.router)
app.include_router(competitors.router)
app.include_router(competitors.process_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
