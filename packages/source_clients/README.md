# packages/source_clients

Shared primitives every connector in `services/ingestion/connectors` builds
on, so rate limiting/retry/raw-storage logic is implemented once, not
per-source.

| Module | Purpose |
|---|---|
| `base.py` | `SourceConnector` Protocol, `SourcePartition`, `RawEnvelope` (spec §34) |
| `rate_limit.py` | `TokenBucket` — async rate limiter sized in requests/minute |
| `retry.py` | `retrying()` decorator + `CircuitBreaker`: exponential backoff+jitter on 5xx, `Retry-After`-aware wait on 429, bounded attempts (§36) |
| `raw_store.py` | `RawStore` Protocol + `LocalFilesystemRawStore` (dev). Mirrors the `raw/<source>/<resource>/ingestion_date=.../<partition>/<sha>.json` S3 layout from §13.1 so an S3-backed store is a drop-in later |

First (and so far only) consumer: `services/ingestion/connectors/khmdhs`.
Schema-validation and metrics/checkpointing pieces of the full connector
contract aren't implemented yet — they land with `services/ingestion/
orchestration` (Στάδιο 1) and the second connector, once there's a real
second use case to generalize from.
