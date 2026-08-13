"""Manual CLI entrypoint.

    python -m services.ingestion.connectors.ted.cli backfill \\
        --date-from 2025-01-01 --date-to 2025-01-30 [--country GR] [--with-vies]

`--country` defaults to `GR` — the platform's whole reason for touching TED
is identifying Greek contracts published EU-wide (§21). Process matching
(`resolve.py`) always runs — it's TED's core value, not an optional extra
the way Διαύγεια/ΓΕΜΗ/ΑΝΑΠΤΥΞΗ are for ΚΗΜΔΗΣ. VIES foreign-supplier
validation is opt-in (needs its own unconfirmed base URL, and only applies
when a notice's supplier isn't Greek in the first place).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date

from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from packages.source_clients.raw_store import configured_raw_store
from services.ingestion.connectors.vies.client import ViesClient
from services.ingestion.connectors.vies.config import ViesConnectorConfig
from services.ingestion.connectors.vies.resolve import check_and_record_vies

from .client import TedClient
from .config import TedConnectorConfig
from .db_writer import TedIngestResult
from .pipeline import ingest_ted_partition
from .resolve import resolve_notice_process_link


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def _make_on_notice_upserted(vies_client: ViesClient | None):
    """Run process matching and optional VIES checks after ingestion."""

    async def _on_notice_upserted(conn: AsyncConnection, result: TedIngestResult) -> None:
        notice = result.notice
        if notice is None:
            return

        process_id = await resolve_notice_process_link(
            conn,
            ted_act_id=notice.act_id,
            buyer_entity_id=notice.buyer_entity_id,
            cpv_codes=notice.cpv_codes,
            publication_date=notice.publication_date,
            title=notice.title,
            amount=notice.amount,
        )
        if process_id is not None:
            print(f"  matched TED notice -> process {process_id}")

        if (
            vies_client is not None
            and notice.supplier_entity_id is not None
            and notice.supplier_country_code
            and notice.supplier_country_code != "GR"
            and notice.supplier_vat
        ):
            valid = await check_and_record_vies(
                conn,
                client=vies_client,
                entity_id=notice.supplier_entity_id,
                country_code=notice.supplier_country_code,
                vat_number=notice.supplier_vat,
            )
            print(f"  VIES check for {notice.supplier_country_code}{notice.supplier_vat}: valid={valid}")

    return _on_notice_upserted


async def _run_backfill(
    country: str,
    date_from: date,
    date_to: date,
    database_url: str,
    raw_root: str,
    with_vies: bool,
) -> None:
    client = TedClient(TedConnectorConfig.from_env())
    raw_store = configured_raw_store(raw_root)
    engine = create_async_engine(_to_asyncpg_url(database_url))

    vies_client = None
    if with_vies:
        vies_client = ViesClient(ViesConnectorConfig.from_env())

    try:
        async with engine.begin() as conn:
            result = await ingest_ted_partition(
                client=client,
                raw_store=raw_store,
                conn=conn,
                country=country,
                date_from=date_from,
                date_to=date_to,
                on_notice_upserted=_make_on_notice_upserted(vies_client),
            )
            print(
                f"pages={result.pages_fetched} "
                f"seen={result.notices_seen} "
                f"ingested={result.notices_ingested} "
                f"failed={result.notices_failed}"
            )
            for failure in result.failed_notices[:10]:
                print(f"  FAILED [{failure['stage']}] {failure['notice_id']}: {failure['error']}")
    finally:
        await client.aclose()
        if vies_client is not None:
            await vies_client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="TED connector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("backfill")
    backfill.add_argument("--date-from", required=True, type=date.fromisoformat)
    backfill.add_argument("--date-to", required=True, type=date.fromisoformat)
    backfill.add_argument("--country", default="GR")
    backfill.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    backfill.add_argument("--raw-root", default="./raw", help="local raw-storage root")
    backfill.add_argument(
        "--with-vies",
        action="store_true",
        help="validate foreign (non-GR) suppliers through the official VIES service (§3.9)",
    )

    args = parser.parse_args()

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("--database-url or $DATABASE_URL is required")

    asyncio.run(
        _run_backfill(
            args.country,
            args.date_from,
            args.date_to,
            database_url,
            args.raw_root,
            with_vies=args.with_vies,
        )
    )


if __name__ == "__main__":
    main()
