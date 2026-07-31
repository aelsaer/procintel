# services/documents

Document pipeline (spec §23/§24): download → MIME validation → SHA-256 →
antivirus scan → original storage → text-layer detection → extraction →
OCR only when required → page segmentation → entity/field extraction →
indexing → evidence references (`field_provenance`, the data behind the
§30.4 evidence drawer). LLM use is limited to grounded summarization/QA/
classification suggestions and never becomes the sole authoritative source
for amounts, dates, legal status or awarded parties (§23.5). The optional
Responses-compatible provider is isolated in `services/intelligence/llm.py`;
without credentials, the endpoints use deterministic extractive fallbacks.

## Status: full pipeline implemented and wired to ingestion

| Module | Purpose |
|---|---|
| `config.py` | `DocumentPipelineConfig` — every numeric guard from §23.2 (max file size, download timeout, decompression-bomb pixel cap, max pages, OCR language/PSM/OEM/timeout) |
| `download.py` | Streamed download with the size cap enforced *while streaming*, not after buffering the whole response |
| `mime.py` | Magic-byte MIME sniffing — never trusts a `Content-Type` header or URL extension |
| `antivirus.py` | `AntivirusScanner` Protocol + environment-aware factory. Production fails closed unless ClamAV is configured; local/test environments may explicitly use the no-op scanner |
| `clamav.py` | `ClamdAntivirusScanner` — the `clamd` daemon's INSTREAM wire protocol over `CLAMD_HOST`/`CLAMD_PORT` or `CLAMD_SOCKET_PATH` |
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
- The production Compose stack includes ClamAV and configures the scheduler
  with `CLAMD_HOST=clamav`. The protocol client is also covered by a fake
  in-process server and an optional live-daemon integration test.
- Tender summaries remain deterministic and evidence-bound through
  `services/intelligence/tender_brief.py`.
- `GET /v1/document-intelligence/processes/{process_id}/search` queries the
  page-level PostgreSQL GIN index. The adjacent `ask` endpoint returns cited
  answers, and `extract-requirements` writes evidence-linked requirements
  into the bid workspace.

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
