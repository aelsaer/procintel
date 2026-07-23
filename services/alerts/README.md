# services/alerts

Tenant alert evaluation, immediate delivery and scheduled digests
(`description.txt` §30.5/§32).

## Implemented

- Events for opportunity/contract create or update, payment detection, buyer
  procurement, company status changes and expiring contracts.
- Rule filters for CPV, buyer, supplier and amount range.
- Immediate, daily-digest and weekly-digest schedules. Daily/weekly events are
  collected without sending an immediate duplicate.
- Timezone-aware digest periods with one digest per rule/period, persisted
  history and included-event counts.
- In-app, SMTP email, signed generic webhook, Teams and Slack delivery targets.
- Webhook delivery persistence, idempotency keys, HMAC signatures, exponential
  retry and terminal failure history.
- Authenticated API/UI creation, editing, pausing, archiving, targets, inbox,
  delivery history and digest history.

`factory.py` builds the production channel multiplexer. `evaluate.py` performs
matching/deduplication; `digests.py` owns scheduled aggregation; delivery
adapters never change procurement truth.

## Workers

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel
python -m services.alerts.cli send-digests
python -m services.alerts.cli retry-webhooks
```

Run both periodically from cron/systemd/Kubernetes. Webhook retries are also
invoked by the ingestion orchestrator unless `--no-webhook-retries` is used.

Email needs `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_FROM_ADDRESS` and optionally `SMTP_USE_TLS`. Webhook/Teams/Slack URLs and
optional signing secrets are stored per rule in `alert_delivery_targets`.

## Operational limits

The current filter vocabulary is deliberately bounded, Teams uses MessageCard
payloads rather than Adaptive Cards, and terminal webhook failures are visible
in product history but do not page a separate on-call system. Scheduling is an
external deployment concern; these commands are idempotent worker entrypoints,
not an embedded distributed scheduler.
