# Geospatial enrichment

This worker extracts the **place of performance**, not merely the buyer's
registered address. For ΚΗΜΔΗΣ it prioritizes `objectDetailsList[].city`, then
`nutsCity`/`nutsPostalCode`, explicit municipality/regional-unit phrases in
titles and descriptions, and finally extracted document text.

Ingestion inserts a row in `geospatial_enrichment_jobs` through a database
trigger and continues immediately. A separate worker resolves the job against
locally loaded `administrative_boundaries`; an optional Nominatim-compatible
provider is used only for unresolved places. Every external result, including
not-found, is cached.

```bash
export DATABASE_URL=postgresql://procintel:procintel@localhost:5432/procintel

# Refresh the local Greek populated-place/admin-unit gazetteer (CC BY 4.0).
python -m services.geospatial.cli load-place-gazetteer

# One-time backfill queue, newest records first.
python -m services.geospatial.cli enqueue-existing --limit 10000

# Local boundary/text extraction only (no external requests).
python -m services.geospatial.cli worker --once --batch-size 100

# Long-running worker.
python -m services.geospatial.cli worker --poll-interval-seconds 15
```

After replacing the gazetteer or changing extraction rules, use
`enqueue-existing --requeue-all` for the desired batch. `--requeue-partial`
only retries records that still lack one or more coordinates.

The GeoNames country dump is downloaded once, not queried per record, and its
Greek/alternate place names are resolved locally. Displayed points include GeoNames
attribution. No external geocoder is enabled by default. For a self-hosted or contracted
Nominatim-compatible service:

```bash
export GEO_GEOCODER_BASE_URL=https://your-geocoder.example
export GEO_GEOCODER_USER_AGENT='Procintel/0.1 (ops@example.com)'
export GEO_GEOCODER_RATE_LIMIT_PER_MINUTE=60
```

The public OSMF Nominatim endpoint must be selected explicitly. When selected,
the worker clamps periodic processing to four requests/minute, uses one serial
worker and caches all queries, per its bulk-geocoding policy. It is not suitable
for the full historical backfill; use local boundaries, a self-hosted instance,
or a commercial provider for that workload.
