from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from packages.domain.tables import (
    act_cpv_codes,
    business_profiles,
    eu_benchmark_snapshots,
    procurement_acts,
    source_records,
    ted_notice_details,
    tenant_cross_border_matches,
)
from services.intelligence.eu_benchmarking import (
    refresh_cross_border_matches_for_tenant,
    refresh_eu_benchmark_snapshots,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL is required for integration tests",
)


def _async_url() -> str:
    assert DATABASE_URL
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_oidc_provisioning_and_european_match_are_persisted_end_to_end() -> None:
    engine = create_async_engine(_async_url())
    source_id = uuid.uuid4()
    act_id = uuid.uuid4()
    suffix = uuid.uuid4().hex
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                first = (
                    await conn.execute(
                        sa.text(
                            """
                            SELECT * FROM procintel_provision_tenant(
                                :issuer, :subject, :email, :organization
                            )
                            """
                        ),
                        {
                            "issuer": "https://identity.integration.test",
                            "subject": f"subject-{suffix}",
                            "email": f"europe-{suffix}@example.test",
                            "organization": "European Integration Supplier",
                        },
                    )
                ).one()
                second = (
                    await conn.execute(
                        sa.text(
                            """
                            SELECT * FROM procintel_provision_tenant(
                                :issuer, :subject, :email, :organization
                            )
                            """
                        ),
                        {
                            "issuer": "https://identity.integration.test",
                            "subject": f"subject-{suffix}",
                            "email": f"europe-{suffix}@example.test",
                            "organization": "Ignored on repeat",
                        },
                    )
                ).one()
                assert first.created is True
                assert second.created is False
                assert second.tenant_id == first.tenant_id

                await conn.execute(
                    business_profiles.insert().values(
                        id=uuid.uuid4(),
                        tenant_id=first.tenant_id,
                        company_name="GIS Supplier",
                        description="Geographic information systems",
                        cpv_prefixes=["72"],
                        keywords=["GIS"],
                        amount_min=10000,
                        amount_max=500000,
                        classification_version=3,
                    )
                )
                await conn.execute(
                    source_records.insert().values(
                        id=source_id,
                        source_system="TED",
                        resource_type="notice",
                        source_native_id=f"PT-{suffix}",
                        content_sha256=suffix,
                        payload_uri=f"/tmp/{suffix}.json",
                        fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                        http_status=200,
                        license_code="EU_REUSE",
                        attribution_text="TED Search API v3",
                        parse_status="PARSED",
                    )
                )
                await conn.execute(
                    procurement_acts.insert().values(
                        id=act_id,
                        act_type="TED_NOTICE",
                        title="GIS platform for municipal asset management",
                        normalized_title="GIS PLATFORM FOR MUNICIPAL ASSET MANAGEMENT",
                        publication_date=date(2026, 7, 15),
                        submission_deadline=datetime(2026, 9, 1, tzinfo=timezone.utc),
                        amount_net=180000,
                        source_record_id=source_id,
                    )
                )
                await conn.execute(
                    act_cpv_codes.insert().values(
                        act_id=act_id,
                        cpv_code="72212326",
                        is_primary=True,
                        source_record_id=source_id,
                    )
                )
                await conn.execute(
                    ted_notice_details.insert().values(
                        id=uuid.uuid4(),
                        act_id=act_id,
                        ted_notice_id=f"PT-{suffix}",
                        publication_number=f"123-{suffix[:6]}",
                        raw_format="JSON",
                        notice_type="competition",
                        parser_version="integration-test",
                        parse_confidence=1,
                        country_code="PT",
                        estimated_value=180000,
                        currency="EUR",
                        source_record_id=source_id,
                    )
                )

                snapshot_count = await refresh_eu_benchmark_snapshots(
                    conn,
                    date_from=date(2026, 7, 1),
                    date_to=date(2026, 7, 31),
                    snapshot_date=date(2099, 1, 1),
                )
                assert snapshot_count >= 1
                cohort = (
                    await conn.execute(
                        sa.select(eu_benchmark_snapshots).where(
                            eu_benchmark_snapshots.c.snapshot_date == date(2099, 1, 1),
                            eu_benchmark_snapshots.c.country_code == "PT",
                            eu_benchmark_snapshots.c.cpv_prefix == "72",
                        )
                    )
                ).first()
                assert cohort is not None
                assert cohort.notice_count >= 1
                assert cohort.dimensions["source"] == "TED Search API v3"

                refresh = await refresh_cross_border_matches_for_tenant(
                    conn,
                    tenant_id=first.tenant_id,
                    as_of=date(2026, 7, 31),
                    publication_from=date(2026, 7, 1),
                    publication_to=date(2026, 7, 31),
                )
                assert refresh.matches_written == 1
                match = (
                    await conn.execute(
                        sa.select(tenant_cross_border_matches).where(
                            tenant_cross_border_matches.c.tenant_id == first.tenant_id,
                            tenant_cross_border_matches.c.act_id == act_id,
                        )
                    )
                ).one()
                assert match.process_id is None
                assert match.profile_version == 3
                assert match.match_score == 100
                assert any("Title fit" in reason for reason in match.reasons)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
