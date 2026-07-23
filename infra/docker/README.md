# infra/docker

PostgreSQL/PostGIS, OpenSearch and the persistent daily ingestion runner.

```bash
cp -n .env.scheduler.example .env
docker compose up -d --build
docker compose logs -f ingestion-scheduler
```

`ingestion-scheduler` runs the complete ingestion cycle once per day at
`DAILY_INGEST_AT` in `DAILY_INGEST_TIMEZONE`. It waits until the scheduled
time on startup.

The database schema must already be migrated. ΓΕΜΗ, ΑΝΑΠΤΥΞΗ and VIES stay
inactive until their key/endpoint values are supplied; logs state this
explicitly on every cycle. ΚΗΜΔΗΣ, TED, Διαύγεια and ΜΕΦ have public defaults.
