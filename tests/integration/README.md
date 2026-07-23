# tests/integration

End-to-end ingestion tests against a real Postgres/PostGIS + OpenSearch stack:
partition ingest → staging → canonicalization → search index update, cursor
advancement semantics under partial failure (spec §35).
