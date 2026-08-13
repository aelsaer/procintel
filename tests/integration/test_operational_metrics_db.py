from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import export_jobs, tenants, users

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_DATABASE_URL = os.environ.get("PROCINTEL_APP_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not APP_DATABASE_URL,
    reason="owner and application DATABASE_URLs are required",
)


def _async_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_operational_function_counts_rls_protected_jobs_without_exposing_rows():
    assert DATABASE_URL and APP_DATABASE_URL
    owner = create_async_engine(_async_url(DATABASE_URL))
    application = create_async_engine(_async_url(APP_DATABASE_URL))
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    try:
        async with owner.begin() as conn:
            await conn.execute(tenants.insert().values(id=tenant_id, name="Metrics RLS"))
            await conn.execute(
                users.insert().values(
                    id=user_id,
                    email=f"metrics-{user_id}@example.test",
                )
            )
            await conn.execute(
                export_jobs.insert().values(
                    id=job_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    export_type="PIPELINE",
                    format="CSV",
                    status="PENDING",
                )
            )

        async with application.connect() as conn:
            direct_count = (
                await conn.execute(
                    sa.text("SELECT COUNT(*) FROM export_jobs WHERE id = :job_id"),
                    {"job_id": job_id},
                )
            ).scalar_one()
            aggregate = (
                await conn.execute(
                    sa.text("SELECT * FROM procintel_operational_queue_metrics()")
                )
            ).one()

        assert direct_count == 0
        assert aggregate.export_queue >= 1
    finally:
        async with owner.begin() as conn:
            await conn.execute(export_jobs.delete().where(export_jobs.c.id == job_id))
            await conn.execute(users.delete().where(users.c.id == user_id))
            await conn.execute(tenants.delete().where(tenants.c.id == tenant_id))
        await application.dispose()
        await owner.dispose()
