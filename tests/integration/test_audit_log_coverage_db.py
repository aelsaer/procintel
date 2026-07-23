"""§40.3 explicitly names `login` and `entity merge/split` as audited
actions; `audit_log` was already written to for workspace/export/alert-rule
actions but not these two. Confirms both now land a real `audit_log` row,
end to end through the real FastAPI app (in-process ASGI transport, no
real HTTP server) using the `PROCINTEL_DEV_AUTH` bypass (`apps/api/auth.py`)
instead of a real OIDC token — that bypass exists specifically so
auth-gated endpoints are testable without a live IdP.

Skipped automatically unless $DATABASE_URL is set.
"""

import os
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.auth import DEV_TENANT_ID
from packages.domain.tables import audit_log, entities, entity_match_candidates

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set — see module docstring")


def _asyncpg_url() -> str:
    if DATABASE_URL.startswith("postgresql://"):
        return "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://") :]
    return DATABASE_URL


async def _seed_merge_candidate(conn) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    entity_a, entity_b = uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        entities.insert().values(
            id=entity_a, entity_type="COMPANY", canonical_name="ALPHA IKE", normalized_name="ALPHA IKE", country_code="GR"
        )
    )
    await conn.execute(
        entities.insert().values(
            id=entity_b, entity_type="COMPANY", canonical_name="ALPHA I.K.E.", normalized_name="ALPHA IKE", country_code="GR"
        )
    )
    candidate_id = uuid.uuid4()
    await conn.execute(
        entity_match_candidates.insert().values(
            id=candidate_id,
            entity_a_id=entity_a,
            entity_b_id=entity_b,
            score=0.95,
            score_breakdown={"name_similarity": 0.95},
            blocking_reason="normalized_name",
        )
    )
    await conn.commit()
    return entity_a, entity_b, candidate_id


async def test_login_and_entity_merge_split_are_audited(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("PROCINTEL_DEV_AUTH", "1")

    from httpx import ASGITransport

    from apps.api.main import app

    engine = create_async_engine(_asyncpg_url())
    try:
        async with engine.connect() as conn:
            entity_a, entity_b, candidate_id = await _seed_merge_candidate(conn)

        try:
            async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
                login_resp = await api_client.post("/v1/workspace/login")
                assert login_resp.status_code == 201
                assert login_resp.json() == {"acknowledged": True}

                review_resp = await api_client.post(
                    f"/v1/entity-review/candidates/{candidate_id}/review",
                    json={"action": "MERGE_A_INTO_B", "notes": "duplicate registration"},
                )
                assert review_resp.status_code == 200

                async with engine.connect() as conn:
                    merge_log_row = (
                        await conn.execute(select(entities.c.merged_into_id).where(entities.c.id == entity_a))
                    ).one()
                    assert merge_log_row.merged_into_id == entity_b

                merges_resp = await api_client.get("/v1/entity-review/merges")
                assert merges_resp.status_code == 200
                merge_id = merges_resp.json()[0]["id"]

                undo_resp = await api_client.post(f"/v1/entity-review/merges/{merge_id}/undo")
                assert undo_resp.status_code == 200

            async with engine.connect() as conn:
                audit_rows = (
                    await conn.execute(
                        select(audit_log.c.action, audit_log.c.object_type, audit_log.c.object_id).where(
                            audit_log.c.tenant_id == uuid.UUID(DEV_TENANT_ID)
                        )
                    )
                ).all()
                actions = {row.action for row in audit_rows}
                assert "login" in actions
                assert "entity.merged" in actions
                assert "entity.split" in actions

                merged_row = next(row for row in audit_rows if row.action == "entity.merged")
                assert merged_row.object_type == "entities"
                assert merged_row.object_id == entity_b  # the surviving entity

                split_row = next(row for row in audit_rows if row.action == "entity.split")
                assert split_row.object_id == entity_a  # the entity that got un-merged
        finally:
            async with engine.connect() as conn:
                await conn.execute(
                    audit_log.delete().where(audit_log.c.tenant_id == uuid.UUID(DEV_TENANT_ID))
                )
                await conn.execute(
                    entity_match_candidates.delete().where(entity_match_candidates.c.id == candidate_id)
                )
                await conn.execute(entities.delete().where(entities.c.id.in_([entity_a, entity_b])))
                await conn.commit()
    finally:
        await engine.dispose()
