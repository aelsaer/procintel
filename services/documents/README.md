# services/documents

Document pipeline (spec §23/§24): download → MIME validation → SHA-256 →
antivirus scan → original storage → text-layer detection → extraction →
OCR only when required → page segmentation → entity/field extraction →
indexing → evidence references (`field_provenance`, the data behind the
§30.4 "evidence drawer"). LLM use is limited to summarization/QA/
classification suggestions — never the sole authoritative source for
amounts, dates, legal status or awarded parties (§23.5); **no LLM
integration exists in this codebase yet** — deliberately not started
without a real provider/cost decision, and none of the extraction targets
below need one to be useful.

## Status: full pipeline implemented and wired to ingestion

| Module | Purpose |
|---|---|
| `config.py` | `DocumentPipelineConfig` — every numeric guard from §23.2 (max file size, download timeout, decompression-bomb pixel cap, max pages, OCR language/PSM/OEM/timeout) |
| `download.py` | Streamed download with the size cap enforced *while streaming*, not after buffering the whole response |
| `mime.py` | Magic-byte MIME sniffing — never trusts a `Content-Type` header or URL extension |
| `antivirus.py` | `AntivirusScanner` Protocol + `NoOpAntivirusScanner` (mirrors the `DeliveryChannel`/`RawStore` pattern) |
| `clamav.py` | `ClamdAntivirusScanner` — a real scanner, the `clamd` daemon's INSTREAM wire protocol over `CLAMD_HOST`/`CLAMD_PORT` or `CLAMD_SOCKET_PATH`. Not the default (no daemon reachable in this sandbox); a pipeline caller opts in explicitly |
| `storage.py` | `DocumentBlobStore` Protocol + `LocalFilesystemDocumentBlobStore` — content-addressed by SHA-256, separate from `packages/source_clients/raw_store.py` (that one lays out dated JSON ingestion partitions, not binary blobs) |
| `pdf_text.py` | `pypdfium2`-based text-layer detection/extraction and page rasterization for OCR fallback |
| `ocr.py` | Tesseract OCR via `subprocess` (the system `tesseract` CLI, not the `pytesseract`/`tesserocr` bindings) — see module docstring for why |
| `amounts.py` | Greek amount parsing (§23.4): all four spec-named formats, VAT-inclusion context detection, never extracts a bare number without an adjacent currency marker |
| `entities.py` | Regex extractors: ΑΔΑ, ΑΔΑΜ, ΑΦΜ (reuses `connectors/khmdhs/afm.py::valid_greek_afm`), CPV, MIS/OPS, dates, protocol numbers, duration, lot numbers, units of measurement, IBAN (opt-in, off by default) |
| `db_writer.py` | Idempotent writes to `documents`/`document_pages`/`field_provenance`, deduped on `documents.sha256` |
| `pipeline.py` | `process_document()` — the one orchestration entrypoint |
| `cli.py` | `python -m services.documents.cli process --url ... [--act-id ...] [--document-type ...]` |

## Operations and remaining infrastructure

- **ΚΗΜΔΗΣ and Διαύγεια documents are wired in.** ΚΗΜΔΗΣ uses the official
  resource attachment endpoint and the daily scheduler processes only
  missing files, bounded by `DAILY_DOCUMENT_MAX_DOWNLOADS` (100 by
  default). Historical files can be resumed with
  `scripts/backfill_documents.py`; `khmdhs/cli.py backfill
  --with-documents` processes both ΚΗΜΔΗΣ attachments and linked Διαύγεια
  PDFs.
- No `clamd` daemon is actually reachable in this sandbox to run
  `ClamdAntivirusScanner` against (not installed, no passwordless `sudo`
  to install one) — the real protocol client exists and is tested (a fake
  in-process server for the wire protocol, a `CLAMD_HOST`-gated
  integration test for the real thing), but `process_document()` still
  defaults to `NoOpAntivirusScanner` unless a caller passes the real one.
- Tender summaries are deterministic and evidence-bound: canonical fields,
  curated provider fields and available document text are used by
  `services/intelligence/tender_brief.py`. An optional generative
  summarization provider is still intentionally unconfigured.
- `document_pages.text_search` (generated `tsvector` column, `13_document_pages.sql`)
  has no API endpoint querying it yet — the storage/indexing side is done,
  nothing in `apps/api` surfaces full-text document search.

## Greek OCR

Works out of the box now, no `apt install`/root access needed: `ell.traineddata`,
`eng.traineddata`, and the `configs/` directory (`tesseract`'s output-format
presets, e.g. the `tsv` config `ocr.py` uses — a fully bundled `TESSDATA_PREFIX`
needs these too, not just the `.traineddata` files, or tesseract can't
resolve the `tsv` argument at all) are bundled directly under
`services/documents/tessdata/` (Apache-2.0 licensed, see `tessdata/LICENSE`
— from the official tesseract-ocr/tessdata project). `ocr.py::_tesseract_env()`
points `TESSDATA_PREFIX` there automatically unless the environment already
sets it explicitly (an operator's own tessdata install always wins).
`tests/contract/test_documents_ocr.py::test_run_ocr_reads_greek_text` runs
real Greek OCR end-to-end, not gated on any special setup beyond `tesseract`
itself being on `PATH`.
