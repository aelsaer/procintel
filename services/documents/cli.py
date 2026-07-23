"""Manual CLI entrypoint.

    python -m services.documents.cli process \\
        --url https://example.test/tender-doc.pdf [--url ...] \\
        [--act-id <uuid>] [--document-type TENDER_DOC] [--title "..."]

Standalone, like TED's and CKAN's CLIs: nothing in the ΚΗΜΔΗΣ/Διαύγεια
connectors triggers this pipeline automatically yet (no connector today
carries a confirmed document-attachment URL field to hook into — see
`services/documents/README.md`); processing a document is an explicit
operator/caller action for now. `--url` is repeatable so a batch of
documents for the same act can be processed in one invocation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

from sqlalchemy.ext.asyncio import create_async_engine

from .config import DocumentPipelineConfig
from .pipeline import process_document


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


async def _process(urls: list[str], *, act_id: uuid.UUID | None, document_type: str, title: str | None, database_url: str) -> None:
    config = DocumentPipelineConfig()
    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.connect() as conn:
            for url in urls:
                result = await process_document(
                    conn,
                    url=url,
                    act_id=act_id,
                    document_type=document_type,
                    title=title,
                    config=config,
                )
                await conn.commit()
                status = "new" if result.is_new else "already processed (deduped by sha256)"
                print(f"{url}: document_id={result.document_id} ({status}), pages={result.page_count}, amounts_found={len(result.amounts)}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_parser = subparsers.add_parser("process", help="Download, extract and index one or more documents")
    process_parser.add_argument("--url", action="append", required=True, dest="urls")
    process_parser.add_argument("--act-id", type=uuid.UUID, default=None)
    process_parser.add_argument("--document-type", default="GENERIC")
    process_parser.add_argument("--title", default=None)
    process_parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))

    args = parser.parse_args()

    if args.command == "process":
        if not args.database_url:
            parser.error("--database-url or $DATABASE_URL is required")
        asyncio.run(
            _process(
                args.urls,
                act_id=args.act_id,
                document_type=args.document_type,
                title=args.title,
                database_url=args.database_url,
            )
        )


if __name__ == "__main__":
    main()
