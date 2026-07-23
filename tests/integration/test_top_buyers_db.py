"""`GET /v1/analytics/top-buyers` against a real Postgres instance.

Mirrors test_analytics_api_consistency_db.py's style: seeds two buyers with
different recorded contract value, confirms ranking order, recorded_value,
act_count and supplier_count, and confirms a superseded (is_current=False)
contract is excluded — the same "current acts only" rule top-suppliers uses.
"""

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.main import app
from packages.domain.tables import act_cpv_codes, act_parties, entities, procurement_acts, procurement_processes, source_records

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_top_buyers_ranks_by_recorded_value_and_ignores_superseded_contracts():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    process_ids = [uuid.uuid4() for _ in range(2)]
    cpv_code = f"98{uuid.uuid4().int % 1_000_000:06d}-1"
    act_ids = [uuid.uuid4() for _ in range(3)]
    buyer_ids = [uuid.uuid4(), uuid.uuid4()]
    supplier_ids = [uuid.uuid4(), uuid.uuid4()]
    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id, source_system="TEST", resource_type="top-buyers-consistency",
                source_native_id=str(source_id), content_sha256=uuid.uuid4().hex,
                payload_uri=f"test://{source_id}", fetched_at=datetime.now(timezone.utc),
                parse_status="PARSED",
            ))
            await conn.execute(entities.insert(), [
                {"id": buyer_ids[0], "entity_type": "ORGANIZATION", "canonical_name": "Unique Top Buyer Municipality", "normalized_name": "UNIQUE TOP BUYER MUNICIPALITY"},
                {"id": buyer_ids[1], "entity_type": "ORGANIZATION", "canonical_name": "Unique Small Buyer Office", "normalized_name": "UNIQUE SMALL BUYER OFFICE"},
                {"id": supplier_ids[0], "entity_type": "COMPANY", "canonical_name": "Unique Top Buyer Supplier A", "normalized_name": "UNIQUE TOP BUYER SUPPLIER A"},
                {"id": supplier_ids[1], "entity_type": "COMPANY", "canonical_name": "Unique Top Buyer Supplier B", "normalized_name": "UNIQUE TOP BUYER SUPPLIER B"},
            ])
            await conn.execute(procurement_processes.insert(), [
                {"id": process_ids[0], "public_id": f"top-buyers-{process_ids[0]}", "title": "Top buyer process"},
                {"id": process_ids[1], "public_id": f"top-buyers-{process_ids[1]}", "title": "Small buyer process"},
            ])
            await conn.execute(procurement_acts.insert(), [
                {
                    "id": act_ids[0], "process_id": process_ids[0], "act_type": "CONTRACT",
                    "title": "Top buyer current contract", "decision_date": date(2098, 6, 10),
                    "amount_gross": Decimal("1000"), "amount_net": Decimal("1000"),
                    "source_record_id": source_id, "is_current": True,
                },
                {
                    "id": act_ids[1], "process_id": process_ids[0], "act_type": "CONTRACT",
                    "title": "Top buyer superseded contract", "decision_date": date(2098, 6, 9),
                    "amount_gross": Decimal("999999"), "amount_net": Decimal("999999"),
                    "source_record_id": source_id, "is_current": False,
                },
                {
                    "id": act_ids[2], "process_id": process_ids[1], "act_type": "CONTRACT",
                    "title": "Small buyer current contract", "decision_date": date(2098, 6, 11),
                    "amount_gross": Decimal("50"), "amount_net": Decimal("50"),
                    "source_record_id": source_id, "is_current": True,
                },
            ])
            await conn.execute(act_cpv_codes.insert(), [
                {"act_id": act_id, "cpv_code": cpv_code, "is_primary": True, "source_record_id": source_id}
                for act_id in act_ids
            ])
            await conn.execute(act_parties.insert(), [
                {"id": uuid.uuid4(), "act_id": act_ids[0], "entity_id": buyer_ids[0], "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[0], "entity_id": supplier_ids[0], "party_role": "SUPPLIER", "amount": Decimal("1000"), "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[1], "entity_id": buyer_ids[0], "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[1], "entity_id": supplier_ids[0], "party_role": "SUPPLIER", "amount": Decimal("999999"), "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[2], "entity_id": buyer_ids[1], "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[2], "entity_id": supplier_ids[1], "party_role": "SUPPLIER", "amount": Decimal("50"), "source_record_id": source_id},
            ])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/analytics/top-buyers", params={
                "date_from": "2098-06-01", "date_to": "2098-06-30",
                "cpv_prefixes": cpv_code.split("-", 1)[0],
            })

        assert response.status_code == 200
        rows = response.json()
        by_id = {row["buyer_id"]: row for row in rows}
        assert str(buyer_ids[0]) in by_id
        assert str(buyer_ids[1]) in by_id

        top = by_id[str(buyer_ids[0])]
        assert Decimal(str(top["recorded_value"])) == Decimal("1000")  # superseded contract excluded
        assert top["act_count"] == 1
        assert top["supplier_count"] == 1
        assert top["buyer_name"] == "Unique Top Buyer Municipality"

        small = by_id[str(buyer_ids[1])]
        assert Decimal(str(small["recorded_value"])) == Decimal("50")

        # ranked by recorded_value descending
        ranked_ids = [row["buyer_id"] for row in rows]
        assert ranked_ids.index(str(buyer_ids[0])) < ranked_ids.index(str(buyer_ids[1]))
    finally:
        async with engine.begin() as conn:
            await conn.execute(act_parties.delete().where(act_parties.c.act_id.in_(act_ids)))
            await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id.in_(act_ids)))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id.in_(act_ids)))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id.in_(process_ids)))
            await conn.execute(entities.delete().where(entities.c.id.in_(buyer_ids + supplier_ids)))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
        await engine.dispose()
