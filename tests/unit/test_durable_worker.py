from services.workers import durable


class _ConnectionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def connect(self):
        return _ConnectionContext()


async def test_run_durable_jobs_once_drains_all_job_types(monkeypatch):
    calls = []

    async def fetch(engine, *, raw_root, limit):
        calls.append(("fetch", raw_root, limit))
        return 2

    async def scoring(conn):
        calls.append(("scoring",))
        return 3

    async def exports(conn, *, limit):
        calls.append(("exports", limit))
        return 4

    async def tenant_ids(conn):
        return []

    async def reminders(conn, client):
        calls.append(("reminders",))
        return {"processed": 5, "sent": 4, "failed": 1}

    monkeypatch.setattr(durable, "process_pending_fetch_requests", fetch)
    monkeypatch.setattr(durable, "process_pending_scoring_jobs", scoring)
    monkeypatch.setattr(durable, "process_pending_export_jobs", exports)
    monkeypatch.setattr(durable, "all_tenant_ids", tenant_ids)
    monkeypatch.setattr(durable, "deliver_due_reminders", reminders)

    result = await durable.run_durable_jobs_once(
        _Engine(), raw_root="/tmp/raw", fetch_limit=7, export_limit=9
    )

    assert result == durable.DurableWorkerResult(
        fetch_requests=2,
        scoring_jobs=3,
        export_jobs=4,
        digests=0,
        webhook_retries=0,
        reminders=5,
    )
    assert calls == [
        ("fetch", "/tmp/raw", 7),
        ("scoring",),
        ("exports", 9),
        ("reminders",),
    ]
