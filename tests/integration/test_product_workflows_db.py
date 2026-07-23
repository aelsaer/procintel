from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.main import app
from packages.domain.tables import procurement_processes


DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_tenant_product_workflows_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("PROCINTEL_DEV_AUTH", "true")
    monkeypatch.setenv("PROCINTEL_DEV_TENANT_ID", str(uuid.uuid4()))
    monkeypatch.setenv("PROCINTEL_DEV_EMAIL", f"workflow-{uuid.uuid4().hex}@example.test")
    monkeypatch.setenv("EXPORT_ROOT", str(tmp_path / "exports"))
    process_id = uuid.uuid4()
    engine = create_async_engine(_async_url())
    try:
        async with engine.connect() as conn:
            await conn.execute(procurement_processes.insert().values(
                id=process_id, public_id=f"workflow-{process_id.hex[:12]}",
                title="Workflow integration opportunity",
            ))
            await conn.commit()
    finally:
        await engine.dispose()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/v1/workspace/me")).status_code == 200

        profile = await client.put("/v1/business-profile", json={
            "company_name": "Workflow Supplier",
            "description": "Υπηρεσίες λογισμικού GIS και cloud",
            "cpv_prefixes": ["72"], "keywords": ["λογισμικό"],
            "nuts_codes": ["EL30"], "buyer_types": [], "procedure_types": [],
            "amount_min": 10000, "classify": True,
        })
        assert profile.status_code == 200
        assert "72" in profile.json()["cpv_prefixes"]
        scoring = await client.get("/v1/business-profile/scoring-status")
        assert scoring.status_code == 200
        assert scoring.json()["status"] in {"QUEUED", "RUNNING", "SUCCEEDED"}
        assert scoring.json()["reason"] == "BUSINESS_PROFILE_CHANGED"

        saved = await client.post("/v1/workspace/saved-searches", json={
            "name": "ICT", "query": {"q": "cloud"},
        })
        assert saved.status_code == 201

        pipeline = await client.post("/v1/workspace/pipeline", json={
            "process_id": str(process_id), "stage": "WATCHING", "priority": "HIGH",
        })
        assert pipeline.status_code == 201
        pipeline_id = pipeline.json()["id"]
        advanced = await client.patch(f"/v1/workspace/pipeline/{pipeline_id}", json={
            "stage": "QUALIFYING", "due_at": "2026-07-31T09:00:00Z",
        })
        assert advanced.status_code == 200
        assert advanced.json()["stage"] == "QUALIFYING"

        note = await client.post("/v1/workspace/notes", json={
            "object_type": "procurement_processes", "object_id": str(process_id),
            "body": "Review eligibility",
        })
        assert note.status_code == 201

        tag = await client.post("/v1/workspace/tags", json={"name": f"priority-{process_id.hex[:6]}"})
        assert tag.status_code == 201
        linked = await client.post(f"/v1/workspace/tags/{tag.json()['id']}/links", json={
            "object_type": "procurement_processes", "object_id": str(process_id),
        })
        assert linked.status_code == 204

        alert = await client.post("/v1/alert-rules", json={
            "name": "ICT daily digest", "event_types": ["opportunity.created"],
            "filters": {"cpv_prefix": "72"}, "schedule": "DAILY_DIGEST",
            "delivery_channels": ["IN_APP"], "timezone": "Europe/Athens",
            "digest_time": "08:00:00", "targets": [],
        })
        assert alert.status_code == 201
        assert alert.json()["schedule"] == "DAILY_DIGEST"

        export = await client.post("/v1/exports", json={
            "export_type": "PIPELINE", "format": "XLSX", "filters": {},
        })
        assert export.status_code == 202
        export_id = export.json()["id"]
        jobs = (await client.get("/v1/exports")).json()
        completed = next(job for job in jobs if job["id"] == export_id)
        assert completed["status"] == "SUCCEEDED"
        assert completed["row_count"] >= 1
