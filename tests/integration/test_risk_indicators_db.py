"""Risk/anomaly indicators (spec §28) against a real Postgres instance.

Only exercises the indicators that read base tables directly
(`high_buyer_concentration`, `repeat_same_contractor`,
`company_inactive_in_later_snapshot`) — the other four
(`few_distinct_suppliers`, `repeated_modifications`, `large_value_increase`,
`unusual_award_to_contract_delay`) depend on materialized views that need a
full `refresh_all_marts` pass to reflect newly inserted fixture rows, which
this codebase's other mart-dependent endpoints (e.g. `/v1/intelligence/
market-dashboard`) also don't have a dedicated fixture-driven integration
test for — consistent depth, not a gap introduced here.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    act_cpv_codes,
    act_parties,
    entities,
    entity_company_snapshots,
    procurement_acts,
    procurement_processes,
    source_records,
)
from services.analytics.risk_indicators import (
    company_inactive_in_later_snapshot,
    high_buyer_concentration,
    repeat_same_contractor,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_high_buyer_concentration_respects_minimum_sample_gate():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    concentrated_buyer = uuid.uuid4()
    thin_buyer = uuid.uuid4()
    supplier = uuid.uuid4()
    process_ids = [uuid.uuid4() for _ in range(8)]
    act_ids = [uuid.uuid4() for _ in range(8)]
    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id, source_system="TEST", resource_type="risk-indicator-concentration",
                source_native_id=str(source_id), content_sha256=uuid.uuid4().hex,
                payload_uri=f"test://{source_id}", fetched_at=datetime.now(timezone.utc),
                parse_status="PARSED",
            ))
            await conn.execute(entities.insert(), [
                {"id": concentrated_buyer, "entity_type": "ORGANIZATION", "canonical_name": "Unique Concentrated Buyer", "normalized_name": "UNIQUE CONCENTRATED BUYER"},
                {"id": thin_buyer, "entity_type": "ORGANIZATION", "canonical_name": "Unique Thin Sample Buyer", "normalized_name": "UNIQUE THIN SAMPLE BUYER"},
                {"id": supplier, "entity_type": "COMPANY", "canonical_name": "Unique Sole Supplier Co", "normalized_name": "UNIQUE SOLE SUPPLIER CO"},
            ])
            await conn.execute(procurement_processes.insert(), [
                {"id": pid, "public_id": f"risk-concentration-{pid}", "title": "Risk concentration process"} for pid in process_ids
            ])
            # 6 contracts for concentrated_buyer, all to the same supplier -> 100% concentration, sample >= 5
            for i in range(6):
                await conn.execute(procurement_acts.insert().values(
                    id=act_ids[i], process_id=process_ids[i], act_type="CONTRACT",
                    title=f"Concentrated contract {i}", is_current=True,
                    amount_net=1000, source_record_id=source_id,
                ))
                await conn.execute(act_parties.insert(), [
                    {"id": uuid.uuid4(), "act_id": act_ids[i], "entity_id": concentrated_buyer, "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                    {"id": uuid.uuid4(), "act_id": act_ids[i], "entity_id": supplier, "party_role": "SUPPLIER", "amount": 1000, "source_record_id": source_id},
                ])
            # 2 contracts for thin_buyer, all to the same supplier -> 100% concentration, but sample < 5
            for i in range(6, 8):
                await conn.execute(procurement_acts.insert().values(
                    id=act_ids[i], process_id=process_ids[i], act_type="CONTRACT",
                    title=f"Thin sample contract {i}", is_current=True,
                    amount_net=1000, source_record_id=source_id,
                ))
                await conn.execute(act_parties.insert(), [
                    {"id": uuid.uuid4(), "act_id": act_ids[i], "entity_id": thin_buyer, "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                    {"id": uuid.uuid4(), "act_id": act_ids[i], "entity_id": supplier, "party_role": "SUPPLIER", "amount": 1000, "source_record_id": source_id},
                ])

        async with engine.connect() as conn:
            instances = await high_buyer_concentration(conn, minimum_contracts=5)

        flagged_buyer_ids = {inst.subject["buyer_id"] for inst in instances}
        assert str(concentrated_buyer) in flagged_buyer_ids
        assert str(thin_buyer) not in flagged_buyer_ids  # below minimum_contracts, correctly excluded

        concentrated_instance = next(inst for inst in instances if inst.subject["buyer_id"] == str(concentrated_buyer))
        assert concentrated_instance.message == "Εντοπίστηκε ασυνήθιστο μοτίβο που απαιτεί περαιτέρω εξέταση."
        assert concentrated_instance.sample_size == 6
        assert concentrated_instance.confidence == "MEDIUM"
        assert float(concentrated_instance.value) == pytest.approx(1.0)
    finally:
        async with engine.begin() as conn:
            await conn.execute(act_parties.delete().where(act_parties.c.act_id.in_(act_ids)))
            await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id.in_(act_ids)))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id.in_(act_ids)))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id.in_(process_ids)))
            await conn.execute(entities.delete().where(entities.c.id.in_([concentrated_buyer, thin_buyer, supplier])))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
        await engine.dispose()


async def test_repeat_same_contractor_scoped_by_buyer_and_cpv():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    buyer = uuid.uuid4()
    repeat_supplier = uuid.uuid4()
    cpv_code = f"99{uuid.uuid4().int % 1_000_000:06d}-2"
    process_ids = [uuid.uuid4() for _ in range(5)]
    act_ids = [uuid.uuid4() for _ in range(5)]
    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id, source_system="TEST", resource_type="risk-indicator-repeat",
                source_native_id=str(source_id), content_sha256=uuid.uuid4().hex,
                payload_uri=f"test://{source_id}", fetched_at=datetime.now(timezone.utc),
                parse_status="PARSED",
            ))
            await conn.execute(entities.insert(), [
                {"id": buyer, "entity_type": "ORGANIZATION", "canonical_name": "Unique Repeat Buyer", "normalized_name": "UNIQUE REPEAT BUYER"},
                {"id": repeat_supplier, "entity_type": "COMPANY", "canonical_name": "Unique Repeat Supplier Co", "normalized_name": "UNIQUE REPEAT SUPPLIER CO"},
            ])
            await conn.execute(procurement_processes.insert(), [
                {"id": pid, "public_id": f"risk-repeat-{pid}", "title": "Risk repeat process"} for pid in process_ids
            ])
            for i in range(5):
                await conn.execute(procurement_acts.insert().values(
                    id=act_ids[i], process_id=process_ids[i], act_type="CONTRACT",
                    title=f"Repeat contract {i}", is_current=True,
                    amount_net=500, source_record_id=source_id,
                ))
                await conn.execute(act_cpv_codes.insert().values(
                    act_id=act_ids[i], cpv_code=cpv_code, is_primary=True, source_record_id=source_id,
                ))
                await conn.execute(act_parties.insert(), [
                    {"id": uuid.uuid4(), "act_id": act_ids[i], "entity_id": buyer, "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                    {"id": uuid.uuid4(), "act_id": act_ids[i], "entity_id": repeat_supplier, "party_role": "SUPPLIER", "amount": 500, "source_record_id": source_id},
                ])

        async with engine.connect() as conn:
            instances = await repeat_same_contractor(conn, minimum_contracts=5)

        matching = [
            inst for inst in instances
            if inst.subject["buyer_id"] == str(buyer) and inst.subject["cpv_prefix_4"] == cpv_code[:4]
        ]
        assert len(matching) == 1
        assert matching[0].sample_size == 5
        assert float(matching[0].value) == pytest.approx(1.0)
        assert matching[0].subject["supplier_id"] == str(repeat_supplier)
    finally:
        async with engine.begin() as conn:
            await conn.execute(act_parties.delete().where(act_parties.c.act_id.in_(act_ids)))
            await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id.in_(act_ids)))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id.in_(act_ids)))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id.in_(process_ids)))
            await conn.execute(entities.delete().where(entities.c.id.in_([buyer, repeat_supplier])))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
        await engine.dispose()


async def test_company_inactive_in_later_snapshot_flags_only_non_active_status():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    dissolved_supplier = uuid.uuid4()
    active_supplier = uuid.uuid4()
    process_ids = [uuid.uuid4() for _ in range(2)]
    act_ids = [uuid.uuid4() for _ in range(2)]
    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id, source_system="TEST", resource_type="risk-indicator-snapshot",
                source_native_id=str(source_id), content_sha256=uuid.uuid4().hex,
                payload_uri=f"test://{source_id}", fetched_at=datetime.now(timezone.utc),
                parse_status="PARSED",
            ))
            await conn.execute(entities.insert(), [
                {"id": dissolved_supplier, "entity_type": "COMPANY", "canonical_name": "Unique Dissolved Supplier Co", "normalized_name": "UNIQUE DISSOLVED SUPPLIER CO"},
                {"id": active_supplier, "entity_type": "COMPANY", "canonical_name": "Unique Active Supplier Co", "normalized_name": "UNIQUE ACTIVE SUPPLIER CO"},
            ])
            await conn.execute(procurement_processes.insert(), [
                {"id": pid, "public_id": f"risk-snapshot-{pid}", "title": "Risk snapshot process"} for pid in process_ids
            ])
            await conn.execute(procurement_acts.insert(), [
                {
                    "id": act_ids[0], "process_id": process_ids[0], "act_type": "CONTRACT",
                    "title": "Active contract with dissolved supplier", "is_current": True,
                    "end_date": None, "amount_net": 100, "source_record_id": source_id,
                },
                {
                    "id": act_ids[1], "process_id": process_ids[1], "act_type": "CONTRACT",
                    "title": "Active contract with active supplier", "is_current": True,
                    "end_date": None, "amount_net": 100, "source_record_id": source_id,
                },
            ])
            await conn.execute(act_parties.insert(), [
                {"id": uuid.uuid4(), "act_id": act_ids[0], "entity_id": dissolved_supplier, "party_role": "SUPPLIER", "amount": 100, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_ids[1], "entity_id": active_supplier, "party_role": "SUPPLIER", "amount": 100, "source_record_id": source_id},
            ])
            await conn.execute(entity_company_snapshots.insert(), [
                {
                    "id": uuid.uuid4(), "entity_id": dissolved_supplier, "source_record_id": source_id,
                    "company_status": "DISSOLVED", "observed_at": datetime.now(timezone.utc),
                    "valid_from": datetime.now(timezone.utc), "is_current": True,
                },
                {
                    "id": uuid.uuid4(), "entity_id": active_supplier, "source_record_id": source_id,
                    "company_status": "ACTIVE", "observed_at": datetime.now(timezone.utc),
                    "valid_from": datetime.now(timezone.utc), "is_current": True,
                },
            ])

        async with engine.connect() as conn:
            instances = await company_inactive_in_later_snapshot(conn)

        flagged_supplier_ids = {inst.subject["supplier_id"] for inst in instances}
        assert str(dissolved_supplier) in flagged_supplier_ids
        assert str(active_supplier) not in flagged_supplier_ids
        flagged = next(inst for inst in instances if inst.subject["supplier_id"] == str(dissolved_supplier))
        assert flagged.value == "DISSOLVED"
        assert flagged.benchmark == "ACTIVE"
    finally:
        async with engine.begin() as conn:
            await conn.execute(entity_company_snapshots.delete().where(entity_company_snapshots.c.entity_id.in_([dissolved_supplier, active_supplier])))
            await conn.execute(act_parties.delete().where(act_parties.c.act_id.in_(act_ids)))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id.in_(act_ids)))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id.in_(process_ids)))
            await conn.execute(entities.delete().where(entities.c.id.in_([dissolved_supplier, active_supplier])))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
        await engine.dispose()
