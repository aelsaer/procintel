# ingestion/normalization

This package documents the normalization ownership boundary. Source-specific
field-name mapping, Greek amount parsing (§23.4), name normalization (§9), and
identifier canonicalization (§7.2) exist,
but as each connector's own `normalize.py`, not centralized here — every
source has different raw field names/casing drift
(`contractRelatedAda`/`contractRelatedADA`), so each connector owns the
mapping for its own payload shape:

| Where the real logic lives |
|---|
| `services/ingestion/connectors/khmdhs/normalize.py` |
| `services/ingestion/connectors/diavgeia/normalize.py` |
| `services/ingestion/connectors/ted/normalize.py` |
| `services/ingestion/connectors/gemi/normalize.py` |
| `services/ingestion/connectors/mef/normalize.py` |
| `services/ingestion/connectors/anaptyxi/normalize.py` |
| `services/ingestion/connectors/ckan/normalize.py` |

Each reads that connector's raw `source_records` payload and produces a
`Normalized*` dataclass/model consumed by that same connector's
`db_writer.py`, which writes the staging/canonical tables and
`field_provenance` entries. `docs/data-dictionary/source-mapping.md` has
the field-by-field mapping across all seven. Each connector's raw amounts
are already-numeric API fields (just `Decimal(str(value))` conversion in
`normalize.py`) — free-text Greek amount *parsing* (§23.4, out of PDF/OCR
text) is a distinct, unrelated concern handled by
`services/documents/amounts.py`. Name matching/canonicalization for
entity dedup is shared in `services/entity_resolution/`
(`find_or_create_entity_by_afm`, `text_similarity.py`). New source field
mappings belong in the relevant connector's `normalize.py`.
