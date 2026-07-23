from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    act_cpv_codes,
    act_locations,
    act_parties,
    entities,
    procurement_acts,
    procurement_processes,
    source_records,
)


DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_market_marts_accept_source_event_date_and_unseeded_cpv_once_per_market():
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    process_id = uuid.uuid4()
    act_id = uuid.uuid4()
    buyer_id, supplier_a, supplier_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cpv_code = f"98{uuid.uuid4().int % 1_000_000:06d}-9"

    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id, source_system="TEST", resource_type="contract",
                source_native_id=str(act_id), content_sha256=uuid.uuid4().hex,
                payload_uri=f"test://{act_id}", fetched_at=datetime.now(timezone.utc),
                parse_status="PARSED",
            ))
            await conn.execute(entities.insert(), [
                {"id": buyer_id, "entity_type": "PUBLIC_BODY", "canonical_name": "Test buyer", "normalized_name": "test buyer"},
                {"id": supplier_a, "entity_type": "COMPANY", "canonical_name": "Supplier A", "normalized_name": "supplier a"},
                {"id": supplier_b, "entity_type": "COMPANY", "canonical_name": "Supplier B", "normalized_name": "supplier b"},
            ])
            await conn.execute(procurement_processes.insert().values(
                id=process_id, public_id=f"analytics-{process_id}", title="Mart regression contract",
                buyer_entity_id=buyer_id,
            ))
            await conn.execute(procurement_acts.insert().values(
                id=act_id, process_id=process_id, act_type="CONTRACT",
                submission_date=date(2026, 6, 15), amount_net=Decimal("100.00"),
                procedure_type="TEST", source_record_id=source_id,
            ))
            await conn.execute(act_cpv_codes.insert().values(
                act_id=act_id, cpv_code=cpv_code, is_primary=False, source_record_id=source_id,
            ))
            await conn.execute(act_locations.insert(), [
                {"id": uuid.uuid4(), "act_id": act_id, "nuts_code": "EL999", "place_text": "Test place A", "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_id, "nuts_code": "EL999", "place_text": "Test place B", "source_record_id": source_id},
            ])
            await conn.execute(act_parties.insert(), [
                {"id": uuid.uuid4(), "act_id": act_id, "entity_id": buyer_id, "party_role": "BUYER", "amount": None, "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_id, "entity_id": supplier_a, "party_role": "SUPPLIER", "amount": Decimal("30.00"), "source_record_id": source_id},
                {"id": uuid.uuid4(), "act_id": act_id, "entity_id": supplier_b, "party_role": "SUPPLIER", "amount": Decimal("70.00"), "source_record_id": source_id},
            ])

            for mart in ("market_value_metrics", "supplier_market_share", "market_hhi"):
                await conn.execute(sa.text(f"REFRESH MATERIALIZED VIEW {mart}"))

            market = (await conn.execute(sa.text(
                """
                SELECT * FROM market_value_metrics
                WHERE cpv_prefix_4=:cpv AND nuts_code='EL999' AND period_year=2026
                """
            ), {"cpv": cpv_code[:4]})).mappings().one()
            assert market["contract_count"] == 1
            assert market["total_value_net"] == Decimal("100.00")
            assert market["supplier_count"] == 2
            assert market["buyer_count"] == 1

            shares = (await conn.execute(sa.text(
                """
                SELECT SUM(supplier_value) AS value, COUNT(*) AS suppliers
                FROM supplier_market_share
                WHERE cpv_prefix_4=:cpv AND nuts_code='EL999' AND period_year=2026
                """
            ), {"cpv": cpv_code[:4]})).mappings().one()
            assert shares["value"] == Decimal("100.00")
            assert shares["suppliers"] == 2

            hhi = (await conn.execute(sa.text(
                """
                SELECT hhi FROM market_hhi
                WHERE cpv_prefix_4=:cpv AND nuts_code='EL999' AND period_year=2026
                """
            ), {"cpv": cpv_code[:4]})).scalar_one()
            assert float(hhi) == pytest.approx(5800.0)

            # Keep the shared integration database clean; materialized views are
            # refreshed again after the test rows are removed.
            await conn.execute(act_parties.delete().where(act_parties.c.act_id == act_id))
            await conn.execute(act_locations.delete().where(act_locations.c.act_id == act_id))
            await conn.execute(act_cpv_codes.delete().where(act_cpv_codes.c.act_id == act_id))
            await conn.execute(procurement_acts.delete().where(procurement_acts.c.id == act_id))
            await conn.execute(procurement_processes.delete().where(procurement_processes.c.id == process_id))
            await conn.execute(entities.delete().where(entities.c.id.in_([buyer_id, supplier_a, supplier_b])))
            await conn.execute(source_records.delete().where(source_records.c.id == source_id))
            for mart in ("market_value_metrics", "supplier_market_share", "market_hhi"):
                await conn.execute(sa.text(f"REFRESH MATERIALIZED VIEW {mart}"))
    finally:
        await engine.dispose()
