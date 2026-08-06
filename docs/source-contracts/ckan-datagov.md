# Source contract: data.gov.gr (CKAN) & INSPIRE

Spec refs: `description.txt` §3.6, §3.7, §22.

## Role

Catalogue of enrichment sources (P3) — demographic, geographic, thematic
context. **Not** treated as a single operational business API; each onboarded
dataset gets its own configuration on top of a generic connector.

## data.gov.gr

- Full production since May 2026, CKAN-based, DCAT-AP compatible.
- Official announcement: 9,000+ datasets, 450+ publishers, 30,000+ resources;
  APIs and bulk downloads in CSV, JSON, XML, XLSX.
- Generic CKAN Action API surface to implement:

  ```
  package_search
  package_show
  resource_search
  ```

  These paths and signed resource-download redirects are live-validated.
  Every onboarded resource records its schema fingerprint and mapping in
  `external_datasets`/`external_dataset_validations`.

## First datasets to onboard (v1)

Administrative boundaries, population per municipality/NUTS, basic school
data, basic hospital data, regional economic indicators, selected
environmental layers. The maintained manifest currently auto-onboards
municipal-unit boundaries and the national school/student dataset; other
datasets use the same validation-first onboarding command when a suitable
official resource is selected.

The scheduler also maintains a deliberately separate, metadata-only
manifest for sources that are useful for provenance or denominator checks
but are not complete operational feeds:

- `companies-prefecture-type`: aggregate ΓΕΜΗ company counts by prefecture
  and legal form; never used for company-level enrichment.
- `erga-espa-anaptyxi`: ΑΝΑΠΤΥΞΗ catalog pointer; its package is not treated
  as a bulk project export.
- `api-ergwn-espa`: documentation for the legacy 2007-2013 API.
- the Ministry of Health hospital/beds package: historical 2017 reference.

These rows use `ingestion_status=METADATA_ONLY`, carry explicit temporal and
geographic scope in `external_datasets.config`, and cannot be dispatched by
the whole-dataset ingestion scheduler.
Missing or zero-byte declared resources create idempotent
`data_quality_issues` entries and are resolved automatically if the catalog
later publishes a usable resource.

## Derived metrics enabled (§22.3)

```
public contracts per resident
project value per student
health procurement value per bed
contracts per municipality
regional dependency on out-of-region suppliers
```

Every such metric must state: denominator year, geographic level, source,
coverage, methodology (`geo_denominators` + presentation layer).

## Κτηματολόγιο INSPIRE Geoportal

Supports search, visualization, download, and invocation of geospatial data.
**v1 scope excludes cadastral parcels** — starts with administrative
boundaries, NUTS, regions, regional units, municipalities, postal codes, and
select environmental/thematic zones where permitted.

The hard-coded cadastral WMS currently remains an auditable health check,
not a parcel-data ingestion path. HTTP 404/410 responses are persisted as
`BLOCKED_UPSTREAM` and retried after 30 days by default.

The Greek Ministry of Environment CSW catalog is queried on every reference
refresh (subject to `INSPIRE_CSW_MAX_RECORDS`). WMS/WFS references discovered
there are deduplicated, validated with `GetCapabilities`, and written to both
`external_datasets` and `spatial_service_capabilities`. Each record preserves
provider, licence/access constraints, catalog modification date, service
formats, layers, health status, and the discovery URL.

Three product-selected view layers are validated on every refresh and exposed
as optional Analytics map overlays:

- high-probability flood-hazard areas (`NZ.Flood`);
- nitrate-vulnerable zones (`AM.NitrateVulnerableZone`);
- nationally protected areas (`PS.ProtectedSite`).

The browser requests these through the allowlisted same-origin
`/v1/analytics/reference-map/{layer_id}` proxy, which fixes mixed-content and
CORS problems without allowing arbitrary upstream URLs, layers, coordinate
systems, or image sizes. Their dataset registry entries explicitly claim
`LIVE_WMS_VIEW` / `REMOTE_RENDERING_ONLY`; WMS pixels are not represented as
locally ingested vector geometries.

Provider load is bounded by `INSPIRE_CSW_MAX_SERVICE_CHECKS` (40 per cycle by
default). Unseen services are checked first, followed by the least recently
checked services, so the catalog converges without a request burst. A valid
capabilities document with no advertised layers is `DEGRADED`, not
`AVAILABLE`. Blocked, invalid, and degraded capabilities are also surfaced in
the shared data-quality review queue and automatically resolved on recovery.

Live validation on 2026-08-06 found that the catalog-advertised WFS download
services expose an empty `FeatureTypeList`; their INSPIRE stored query also
fails because the declared feature type is absent from the server catalog.
Those services remain `DEGRADED`. The platform does not infer polygons from
WMS images or claim thematic vector ingestion until the upstream WFS contract
works. Administrative region and regional-unit geometry is still loaded from
Eurostat GISCO NUTS 2024, and postal codes are loaded as official TERCET
postal-to-NUTS mappings rather than fabricated postal polygons.

Live validation on 2026-08-02 found that the official CSW host answers over
HTTP but refuses HTTPS. The default therefore uses the published HTTP URL and
stores all fetched metadata with a content hash; it must be upgraded to HTTPS
as soon as the provider supports it. Individual discovered service URLs keep
the transport declared by the official catalog.
