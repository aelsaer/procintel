from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.main import app
from packages.domain.tables import (
    act_cpv_codes,
    act_locations,
    procurement_acts,
    procurement_processes,
    procurement_signals,
    source_records,
)


DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_date_and_geography_scope_is_shared_by_product_endpoints(monkeypatch):
    tenant_id = uuid.uuid4()
    source_id = uuid.uuid4()
    process_ids = [uuid.uuid4(), uuid.uuid4()]
    notice_ids = [uuid.uuid4(), uuid.uuid4()]
    contract_ids = [uuid.uuid4(), uuid.uuid4()]
    signal_ids = [uuid.uuid4(), uuid.uuid4()]
    token = f"scopeprobe{uuid.uuid4().hex[:10]}"
    cpv_code = "99980000"
    engine = create_async_engine(_async_url())

    monkeypatch.setenv("PROCINTEL_DEV_AUTH", "true")
    monkeypatch.setenv("PROCINTEL_DEV_TENANT_ID", str(tenant_id))
    monkeypatch.setenv("PROCINTEL_DEV_EMAIL", f"{token}@example.test")

    try:
        async with engine.begin() as conn:
            await conn.execute(source_records.insert().values(
                id=source_id,
                source_system="TEST",
                resource_type="WORKSPACE_SCOPE",
                source_native_id=token,
                content_sha256=uuid.uuid4().hex * 2,
                payload_uri=f"test://{token}",
                fetched_at=datetime.now(timezone.utc),
                license_code="TEST",
                attribution_text="Workspace scope integration fixture",
                parse_status="PARSED",
            ))
            await conn.execute(procurement_processes.insert(), [
                {"id": process_ids[0], "public_id": f"{token}-athens", "title": f"{token} Athens"},
                {"id": process_ids[1], "public_id": f"{token}-crete", "title": f"{token} Crete"},
            ])
            await conn.execute(procurement_acts.insert(), [
                {
                    "id": notice_ids[0], "process_id": process_ids[0], "act_type": "NOTICE",
                    "title": f"{token} Athens notice", "publication_date": date(2098, 6, 15),
                    "decision_date": None, "amount_net": 100, "source_record_id": source_id,
                },
                {
                    "id": notice_ids[1], "process_id": process_ids[1], "act_type": "NOTICE",
                    "title": f"{token} Crete notice", "publication_date": date(2098, 7, 15),
                    "decision_date": None, "amount_net": 200, "source_record_id": source_id,
                },
                {
                    "id": contract_ids[0], "process_id": process_ids[0], "act_type": "CONTRACT",
                    "title": f"{token} Athens contract", "publication_date": None,
                    "decision_date": date(2098, 6, 20),
                    "amount_net": 100, "source_record_id": source_id,
                },
                {
                    "id": contract_ids[1], "process_id": process_ids[1], "act_type": "CONTRACT",
                    "title": f"{token} Crete contract", "publication_date": None,
                    "decision_date": date(2098, 7, 20),
                    "amount_net": 200, "source_record_id": source_id,
                },
            ])
            await conn.execute(act_cpv_codes.insert(), [
                {"act_id": act_id, "cpv_code": cpv_code, "is_primary": True, "source_record_id": source_id}
                for act_id in [*notice_ids, *contract_ids]
            ])
            await conn.execute(act_locations.insert(), [
                {
                    "id": uuid.uuid4(), "act_id": act_id, "nuts_code": "EL30",
                    "municipality_name": "Αθήνα", "regional_unit_name": "Κεντρικός Τομέας Αθηνών",
                    "country_code": "GR", "source_record_id": source_id,
                }
                for act_id in (notice_ids[0], contract_ids[0])
            ] + [
                {
                    "id": uuid.uuid4(), "act_id": act_id, "nuts_code": "EL43",
                    "municipality_name": "Ηράκλειο", "regional_unit_name": "Ηρακλείου",
                    "country_code": "GR", "source_record_id": source_id,
                }
                for act_id in (notice_ids[1], contract_ids[1])
            ])
            await conn.execute(procurement_signals.insert(), [
                {
                    "id": signal_ids[0], "signal_type": "EARLY_REQUEST",
                    "title": f"{token} Athens signal", "source_record_id": source_id,
                    "publication_date": date(2098, 6, 10), "expected_notice_date": date(2098, 6, 15),
                    "estimated_value": 100, "cpv_codes": [cpv_code], "nuts_codes": ["EL30"],
                    "confidence": 0.9, "linked_process_id": process_ids[0],
                },
                {
                    "id": signal_ids[1], "signal_type": "PROCUREMENT_PLAN",
                    "title": f"{token} Crete signal", "source_record_id": source_id,
                    "publication_date": date(2098, 7, 10), "expected_notice_date": date(2098, 7, 15),
                    "estimated_value": 200, "cpv_codes": [cpv_code], "nuts_codes": ["EL43"],
                    "confidence": 0.9, "linked_process_id": process_ids[1],
                },
            ])

        june_athens = {
            "date_from": "2098-06-01",
            "date_to": "2098-06-30",
            "nuts_code": "EL30",
            "municipality": "Αθήνα",
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            opportunities = await client.get("/v1/intelligence/opportunities", params=june_athens)
            assert opportunities.status_code == 200
            assert [row["process_id"] for row in opportunities.json()] == [str(process_ids[0])]

            archive = await client.get("/v1/search", params={"q": token, **june_athens})
            assert archive.status_code == 200
            assert {row["process_id"] for row in archive.json()["data"]} == {str(process_ids[0])}

            signals = await client.get("/v1/intelligence/signals", params=june_athens)
            assert signals.status_code == 200
            assert [row["id"] for row in signals.json()] == [str(signal_ids[0])]

            dashboard = await client.get(
                "/v1/intelligence/market-dashboard",
                params={"cpv_prefixes": cpv_code, **june_athens},
            )
            assert dashboard.status_code == 200
            assert dashboard.json()["summary"]["contract_count"] == 1
            assert float(dashboard.json()["summary"]["total_value"]) == 100
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.delete(procurement_signals).where(procurement_signals.c.id.in_(signal_ids)))
            await conn.execute(sa.delete(act_locations).where(
                act_locations.c.act_id.in_([*notice_ids, *contract_ids])
            ))
            await conn.execute(sa.delete(act_cpv_codes).where(
                act_cpv_codes.c.act_id.in_([*notice_ids, *contract_ids])
            ))
            await conn.execute(sa.delete(procurement_acts).where(
                procurement_acts.c.id.in_([*notice_ids, *contract_ids])
            ))
            await conn.execute(sa.delete(procurement_processes).where(
                procurement_processes.c.id.in_(process_ids)
            ))
            await conn.execute(sa.delete(source_records).where(source_records.c.id == source_id))
        await engine.dispose()
