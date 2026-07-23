from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.main import app
from packages.domain.tables import act_cpv_codes, procurement_acts, procurement_processes, source_records


DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_market_overview_uses_current_acts_and_coherent_opportunity_counts():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    process_id = uuid.uuid4()
    cpv_code = f"97{uuid.uuid4().int % 1_000_000:06d}-9"
    act_ids = [uuid.uuid4() for _ in range(4)]
    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id,
                source_system="TEST",
                resource_type="analytics-consistency",
                source_native_id=str(source_id),
                content_sha256=uuid.uuid4().hex,
                payload_uri=f"test://{source_id}",
                fetched_at=datetime.now(timezone.utc),
                parse_status="PARSED",
            ))
            await conn.execute(procurement_processes.insert().values(
                id=process_id,
                public_id=f"analytics-consistency-{process_id}",
                title="Unique analytics consistency process",
            ))
            await conn.execute(procurement_acts.insert(), [
                {
                    "id": act_ids[0], "process_id": process_id, "act_type": "REQUEST",
                    "title": "Unique vegetation request", "publication_date": date(2098, 6, 2),
                    "decision_date": None,
                    "amount_gross": Decimal("50"), "source_record_id": source_id, "is_current": True,
                },
                {
                    "id": act_ids[1], "process_id": process_id, "act_type": "NOTICE",
                    "title": "Unique vegetation notice", "publication_date": date(2098, 6, 3),
                    "decision_date": None,
                    "amount_gross": Decimal("80"), "source_record_id": source_id, "is_current": True,
                },
                {
                    "id": act_ids[2], "process_id": process_id, "act_type": "CONTRACT",
                    "title": "Unique vegetation contract", "publication_date": None,
                    "decision_date": date(2098, 6, 10),
                    "amount_gross": Decimal("100"), "source_record_id": source_id, "is_current": True,
                },
                {
                    "id": act_ids[3], "process_id": process_id, "act_type": "CONTRACT",
                    "title": "Superseded vegetation contract", "publication_date": None,
                    "decision_date": date(2098, 6, 9),
                    "amount_gross": Decimal("999"), "source_record_id": source_id, "is_current": False,
                },
            ])
            await conn.execute(act_cpv_codes.insert(), [
                {"act_id": act_id, "cpv_code": cpv_code, "is_primary": True, "source_record_id": source_id}
                for act_id in act_ids
            ])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/analytics/market-overview", params={
                "date_from": "2098-06-01",
                "date_to": "2098-06-30",
                "cpv_prefixes": cpv_code.split("-", 1)[0],
            })

        assert response.status_code == 200
        metrics = response.json()
        assert metrics["process_count"] == 1
        assert metrics["act_count"] == 3
        assert metrics["opportunity_count"] == 2
        assert metrics["notice_count"] == 1
        assert metrics["contract_count"] == 1
        assert Decimal(metrics["recorded_contract_value"]) == Decimal("100")
        assert metrics["notice_count"] <= metrics["opportunity_count"] <= metrics["act_count"]
    finally:
        async with engine.begin() as conn:
            await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id.in_(act_ids)))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id.in_(act_ids)))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id == process_id))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
        await engine.dispose()
