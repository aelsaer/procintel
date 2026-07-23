"""`GET /v1/analytics/region-activity` against a real Postgres instance.

The map's "what exists here" drill-down — unlike `/opportunities` it is not
restricted to opportunity act types, so a clicked region can show contracts
too. Seeds a CONTRACT and a NOTICE in the same NUTS region plus a CONTRACT in
a different region, confirming: region filtering, `act_types` filtering, and
that an unrelated region's act is excluded.
"""

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.main import app
from packages.domain.tables import act_cpv_codes, act_locations, act_parties, entities, procurement_acts, procurement_processes, source_records

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_region_activity_filters_by_nuts_region_and_act_type():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    cpv_code = f"71{uuid.uuid4().int % 1_000_000:06d}-3"
    process_ids = [uuid.uuid4() for _ in range(3)]
    act_ids = [uuid.uuid4() for _ in range(3)]
    buyer_id = uuid.uuid4()
    target_region = "EL999"  # deliberately unused real prefix, unique per test run avoided via act_locations scoping below
    other_region = "EL888"
    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id, source_system="TEST", resource_type="region-activity-consistency",
                source_native_id=str(source_id), content_sha256=uuid.uuid4().hex,
                payload_uri=f"test://{source_id}", fetched_at=datetime.now(timezone.utc),
                parse_status="PARSED",
            ))
            await conn.execute(entities.insert().values(
                id=buyer_id, entity_type="ORGANIZATION",
                canonical_name="Unique Region Activity Buyer", normalized_name="UNIQUE REGION ACTIVITY BUYER",
            ))
            await conn.execute(procurement_processes.insert(), [
                {"id": process_ids[0], "public_id": f"region-activity-{process_ids[0]}", "title": "Region contract process"},
                {"id": process_ids[1], "public_id": f"region-activity-{process_ids[1]}", "title": "Region notice process"},
                {"id": process_ids[2], "public_id": f"region-activity-{process_ids[2]}", "title": "Other region process"},
            ])
            await conn.execute(procurement_acts.insert(), [
                {
                    "id": act_ids[0], "process_id": process_ids[0], "act_type": "CONTRACT",
                    "title": "Target region contract", "decision_date": date(2098, 6, 10), "publication_date": None,
                    "amount_gross": Decimal("1000"), "amount_net": Decimal("1000"),
                    "source_record_id": source_id, "is_current": True,
                },
                {
                    "id": act_ids[1], "process_id": process_ids[1], "act_type": "NOTICE",
                    "title": "Target region notice", "decision_date": None, "publication_date": date(2098, 6, 5),
                    "amount_gross": Decimal("2000"), "amount_net": Decimal("2000"),
                    "source_record_id": source_id, "is_current": True,
                },
                {
                    "id": act_ids[2], "process_id": process_ids[2], "act_type": "CONTRACT",
                    "title": "Other region contract", "decision_date": date(2098, 6, 11), "publication_date": None,
                    "amount_gross": Decimal("3000"), "amount_net": Decimal("3000"),
                    "source_record_id": source_id, "is_current": True,
                },
            ])
            await conn.execute(act_cpv_codes.insert(), [
                {"act_id": act_id, "cpv_code": cpv_code, "is_primary": True, "source_record_id": source_id}
                for act_id in act_ids
            ])
            await conn.execute(act_parties.insert(), [
                {"id": uuid.uuid4(), "act_id": act_ids[0], "entity_id": buyer_id, "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[1], "entity_id": buyer_id, "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[2], "entity_id": buyer_id, "party_role": "BUYER", "amount": None, "source_record_id": source_id},
            ])
            await conn.execute(act_locations.insert(), [
                {"id": uuid.uuid4(), "act_id": act_ids[0], "nuts_code": target_region, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[1], "nuts_code": target_region, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[2], "nuts_code": other_region, "source_record_id": source_id},
            ])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            all_in_region = await client.get("/v1/analytics/region-activity", params={
                "nuts_code": target_region,
                "cpv_prefixes": cpv_code.split("-", 1)[0],
            })
            contracts_only = await client.get("/v1/analytics/region-activity", params={
                "nuts_code": target_region,
                "cpv_prefixes": cpv_code.split("-", 1)[0],
                "act_types": "CONTRACT",
            })

        assert all_in_region.status_code == 200
        region_ids = {row["act_id"] for row in all_in_region.json()}
        assert str(act_ids[0]) in region_ids
        assert str(act_ids[1]) in region_ids
        assert str(act_ids[2]) not in region_ids  # different region, excluded

        assert contracts_only.status_code == 200
        contract_ids = {row["act_id"] for row in contracts_only.json()}
        assert str(act_ids[0]) in contract_ids
        assert str(act_ids[1]) not in contract_ids  # NOTICE excluded by act_types filter

        target_row = next(row for row in all_in_region.json() if row["act_id"] == str(act_ids[0]))
        assert target_row["buyer_name"] == "Unique Region Activity Buyer"
        assert Decimal(str(target_row["amount_gross"])) == Decimal("1000")
        assert target_row["act_type"] == "CONTRACT"
    finally:
        async with engine.begin() as conn:
            await conn.execute(act_locations.delete().where(act_locations.c.act_id.in_(act_ids)))
            await conn.execute(act_parties.delete().where(act_parties.c.act_id.in_(act_ids)))
            await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id.in_(act_ids)))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id.in_(act_ids)))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id.in_(process_ids)))
            await conn.execute(entities.delete().where(entities.c.id == buyer_id))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
        await engine.dispose()
