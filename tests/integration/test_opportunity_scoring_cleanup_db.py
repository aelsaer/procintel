import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    opportunity_scores,
    procurement_processes,
    tenants,
)
from services.analytics.opportunity_scoring import (
    score_opportunities_for_tenant,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set",
)


def _asyncpg_url() -> str:
    if DATABASE_URL.startswith("postgresql://"):
        return "postgresql+asyncpg://" + DATABASE_URL.removeprefix(
            "postgresql://"
        )
    return DATABASE_URL


async def test_full_rescore_removes_scores_when_profile_has_no_rules() -> None:
    engine = create_async_engine(_asyncpg_url())
    tenant_id = uuid.uuid4()
    try:
        async with engine.connect() as conn:
            process_id = (
                await conn.execute(
                    sa.select(procurement_processes.c.id).limit(1)
                )
            ).scalar_one()
            await conn.execute(
                tenants.insert().values(
                    id=tenant_id,
                    name="No rules scoring cleanup",
                )
            )
            await conn.execute(
                opportunity_scores.insert().values(
                    id=uuid.uuid4(),
                    process_id=process_id,
                    tenant_id=tenant_id,
                    total_score=50,
                    cpv_company_fit_score=10,
                    buyer_affinity_score=10,
                    timing_score=10,
                    competitive_attractiveness_score=5,
                    contract_value_fit_score=10,
                    data_confidence_score=5,
                    evidence=[],
                )
            )
            await conn.commit()

            result = await score_opportunities_for_tenant(
                conn,
                tenant_id=tenant_id,
            )

            assert result.rules_considered == 0
            remaining = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(opportunity_scores)
                    .where(opportunity_scores.c.tenant_id == tenant_id)
                )
            ).scalar_one()
            assert remaining == 0

            await conn.execute(
                tenants.delete().where(tenants.c.id == tenant_id)
            )
            await conn.commit()
    finally:
        await engine.dispose()
