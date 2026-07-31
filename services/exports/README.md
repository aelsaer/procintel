# services/exports

CSV/XLSX export generation for `apps/api/routers/exports.py`'s
`POST /v1/exports` (`ExportCreateRequest.export_type` — `PIPELINE`,
`SUPPLIERS`, `BUYERS`, `RELATIONSHIPS`, `OPPORTUNITIES`) — no third-party
export/reporting service, both formats are written by hand here.

| Module | Purpose |
|---|---|
| `generate.py` | `_export_rows()` — tenant-scoped export queries with source attribution. `process_export_job()` writes under `EXPORT_ROOT/<tenant_id>/` and records a seven-day expiry. `cleanup_expired_exports()` expires jobs and deletes only paths proven to be contained by `EXPORT_ROOT`. CSV uses UTF-8 BOM for Greek Excel compatibility; XLSX is a minimal single-sheet OOXML package. |
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

The daily orchestration cycle runs the expiry sweep. Every export is a fresh
snapshot query by design; large user exports should be drained with the CLI
worker instead of the in-process background callback.
