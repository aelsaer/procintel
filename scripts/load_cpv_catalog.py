#!/usr/bin/env python3
"""Load the complete multilingual CPV 2008 catalogue from official OP-TED data.

The default source is the Genericode file maintained by the Publications
Office. A local ``.gc`` file can be supplied for repeatable/offline runs.

Example:
    DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel \
    python scripts/load_cpv_catalog.py

    python scripts/load_cpv_catalog.py --source /tmp/cpv.gc
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SOURCE = "https://raw.githubusercontent.com/OP-TED/eForms-SDK/1.15.1/codelists/cpv.gc"

cpv_codes = sa.table(
    "cpv_codes",
    sa.column("code", sa.Text),
    sa.column("check_digit", sa.Text),
    sa.column("prefix_2", sa.Text),
    sa.column("prefix_3", sa.Text),
    sa.column("prefix_4", sa.Text),
    sa.column("prefix_5", sa.Text),
    sa.column("parent_code", sa.Text),
    sa.column("description_el", sa.Text),
    sa.column("description_en", sa.Text),
)


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
    return database_url


def _row_values(row: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for value in row.findall("./Value"):
        simple = value.find("./SimpleValue")
        if simple is not None and simple.text:
            values[value.attrib["ColumnRef"]] = simple.text.strip()
    return values


def parse_cpv_rows(path: Path) -> Iterator[dict[str, str | None]]:
    """Stream CPV rows without holding the 30+ MB source document in memory."""
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag.rsplit("}", 1)[-1] != "Row":
            continue
        values = _row_values(element)
        code = values.get("code", "")
        if len(code) == 8 and code.isdigit():
            yield {
                "code": code,
                "check_digit": None,
                "prefix_2": code[:2],
                "prefix_3": code[:3],
                "prefix_4": code[:4],
                "prefix_5": code[:5],
                "parent_code": values.get("parentCode"),
                "description_el": values.get("ell_label"),
                "description_en": values.get("eng_label") or values.get("Name"),
            }
        element.clear()


def _batches(rows: list[dict[str, str | None]], size: int) -> Iterator[list[dict[str, str | None]]]:
    for offset in range(0, len(rows), size):
        yield rows[offset : offset + size]


def _resolve_source(source: str) -> tuple[Path, bool]:
    if not source.startswith(("http://", "https://")):
        return Path(source).expanduser().resolve(), False
    handle = tempfile.NamedTemporaryFile(prefix="procintel-cpv-", suffix=".gc", delete=False)
    handle.close()
    destination = Path(handle.name)
    urllib.request.urlretrieve(source, destination)
    return destination, True


async def load_catalog(database_url: str, source_path: Path, batch_size: int = 500) -> int:
    rows = list(parse_cpv_rows(source_path))
    if len(rows) < 9_000:
        raise RuntimeError(f"CPV source is incomplete: expected at least 9,000 rows, got {len(rows)}")

    engine = create_async_engine(_to_asyncpg_url(database_url))
    try:
        async with engine.begin() as conn:
            for batch in _batches(rows, batch_size):
                values_without_parents = [{**row, "parent_code": None} for row in batch]
                statement = pg_insert(cpv_codes).values(values_without_parents)
                await conn.execute(
                    statement.on_conflict_do_update(
                        index_elements=["code"],
                        set_={
                            "prefix_2": statement.excluded.prefix_2,
                            "prefix_3": statement.excluded.prefix_3,
                            "prefix_4": statement.excluded.prefix_4,
                            "prefix_5": statement.excluded.prefix_5,
                            "description_el": statement.excluded.description_el,
                            "description_en": statement.excluded.description_en,
                        },
                    )
                )
            for batch in _batches(rows, batch_size):
                await conn.execute(
                    sa.text(
                        """
                        UPDATE cpv_codes
                        SET parent_code = :parent_code
                        WHERE code = :code
                        """
                    ),
                    [{"code": row["code"], "parent_code": row["parent_code"]} for row in batch],
                )
    finally:
        await engine.dispose()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or $DATABASE_URL is required")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    source_path, temporary = _resolve_source(args.source)
    try:
        count = asyncio.run(load_catalog(args.database_url, source_path, args.batch_size))
    finally:
        if temporary:
            source_path.unlink(missing_ok=True)
    print(f"cpv_catalog_loaded={count}")


if __name__ == "__main__":
    main()
