# services/exports

CSV/XLSX export generation for `apps/api/routers/exports.py`'s
`POST /v1/exports` (`ExportCreateRequest.export_type` — `PIPELINE`,
`SUPPLIERS`, `BUYERS`, `RELATIONSHIPS`, `OPPORTUNITIES`) — no third-party
export/reporting service, both formats are written by hand here.

| Module | Purpose |
|---|---|
| `generate.py` | `_export_rows()` — one hand-written tenant-scoped SQL query per `export_type`, joining the canonical tables (`procurement_processes`, `act_parties`, `entities`, ...), always carrying a `source_attribution` column into the output (spec's provenance-in-exports requirement). `process_export_job()` claims a `export_jobs` row (`SELECT ... FOR UPDATE`, `PENDING`/`FAILED` only), writes the file under `EXPORT_ROOT/<tenant_id>/`, and records `SUCCEEDED`/`FAILED` + `expires_at` (7 days). `_write_csv()` uses the stdlib `csv` module (`utf-8-sig`, so Excel opens Greek text correctly); `_write_xlsx()` hand-writes the minimal OOXML `.xlsx` package (`[Content_Types].xml`/`workbook.xml`/one worksheet, `zipfile` + string templates) — no `openpyxl`/`xlsxwriter` dependency for what is deliberately a single-sheet, unstyled export. |
| `cli.py` | `python -m services.exports.cli --limit 25` — drains pending/failed `export_jobs` outside the API process, same shape as `services/documents/cli.py`; not wired into the orchestration scheduler (exports are triggered per-request, not on a schedule) |

## How a job actually runs

`POST /v1/exports` inserts a `PENDING` `export_jobs` row and returns
immediately; the row is processed by a FastAPI `BackgroundTasks` callback
(`process_export_job_by_id`, added via `background_tasks.add_task(...)`
right in the route handler) rather than synchronously in the request or
via the orchestration scheduler — an export is a one-off, user-triggered
action, not a recurring ingestion job. `cli.py` exists alongside this for
retrying stuck `FAILED` jobs (e.g. after a deploy) without going through
the API.

## Not yet implemented

- No expired-file cleanup sweep — `expires_at` is recorded but nothing
  deletes the on-disk file or the row once it passes.
- Every export is a full re-query on demand; nothing caches or paginates
  a very large `RELATIONSHIPS`/`SUPPLIERS` export beyond what the SQL
  query itself returns.
