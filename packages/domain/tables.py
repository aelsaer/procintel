"""SQLAlchemy Core table definitions mirroring db/migrations/*.sql.

Only the tables the ΚΗΜΔΗΣ connector (all five resources + adamChain/process
grouping) touches are defined here — more get added as later
connectors/slices need them (db/migrations remains the source of truth for
DDL; this module is query-building metadata only, it never calls
`metadata.create_all()`). Column names/types are kept in sync by hand with
db/migrations/01_extensions_and_source_layer.sql, 02_identity_and_registry.sql
and 03_procurement_core.sql — if you change one, change the other.

`act_locations.geom` (PostGIS geometry) is still intentionally omitted:
the ΚΗΜΔΗΣ connector never writes it (contract records don't carry
coordinates). `administrative_boundaries.geom` *is* modeled, using
GeoAlchemy2's `Geometry` type — the CKAN connector's boundaries adapter
(`services/ingestion/connectors/ckan/boundaries.py`) is the first slice
that actually needs to write geometry.
"""

from __future__ import annotations

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData()

source_records = sa.Table(
    "source_records",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("source_system", sa.Text, nullable=False),
    sa.Column("resource_type", sa.Text, nullable=False),
    sa.Column("source_native_id", sa.Text),
    sa.Column("source_version", sa.Text),
    sa.Column("content_sha256", sa.Text, nullable=False),
    sa.Column("payload_uri", sa.Text, nullable=False),
    sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("source_updated_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("http_status", sa.Integer),
    sa.Column("schema_version", sa.Text),
    sa.Column("license_code", sa.Text),
    sa.Column("request_url_hash", sa.Text),
    sa.Column("parse_status", sa.Text, nullable=False, server_default="PENDING"),
    sa.Column("parse_error", JSONB),
    sa.Column("is_latest", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

connector_runs = sa.Table(
    "connector_runs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("source_system", sa.Text, nullable=False),
    sa.Column("resource_type", sa.Text, nullable=False),
    sa.Column("partition_key", sa.Text, nullable=False),
    sa.Column("run_type", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="RUNNING"),
    sa.Column("pages_fetched", sa.Integer, nullable=False, server_default="0"),
    sa.Column("records_fetched", sa.Integer, nullable=False, server_default="0"),
    sa.Column("records_upserted", sa.Integer, nullable=False, server_default="0"),
    sa.Column("rate_limit_hits", sa.Integer, nullable=False, server_default="0"),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("error", JSONB),
    sa.Column("triggered_by", sa.Text),
)

fetch_requests = sa.Table(
    "fetch_requests",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("identifier_raw", sa.Text, nullable=False),
    sa.Column("identifier_normalized", sa.Text, nullable=False),
    sa.Column("identifier_scheme", sa.Text, nullable=False),
    sa.Column("source_system", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="QUEUED"),
    sa.Column("message", sa.Text),
    sa.Column("result_act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id")),
    sa.Column("result_process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id")),
    sa.Column("requested_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("next_retry_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("request_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

source_cursors = sa.Table(
    "source_cursors",
    metadata,
    sa.Column("source_system", sa.Text, primary_key=True),
    sa.Column("resource_type", sa.Text, primary_key=True),
    sa.Column("partition_key", sa.Text, primary_key=True),
    sa.Column("cursor_value", JSONB, nullable=False),
    sa.Column("last_success_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_error", JSONB),
)

external_datasets = sa.Table(
    "external_datasets",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("catalog_source", sa.Text, nullable=False),
    sa.Column("catalog_dataset_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("publisher", sa.Text),
    sa.Column("license_code", sa.Text),
    sa.Column("resource_type", sa.Text),
    sa.Column("resource_url", sa.Text),
    sa.Column("update_frequency", sa.Text),
    sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("ingestion_status", sa.Text, nullable=False, server_default="NOT_ONBOARDED"),
    sa.Column("adapter_name", sa.Text),
    sa.Column("config", JSONB),
)

# geo_denominators.nuts_code is a soft reference to nuts_areas(code) — that
# reference table isn't modeled here yet (nothing in this pass writes to
# it), same "looked up by, not hard-FK'd from" convention already used by
# act_locations.nuts_code.
geo_denominators = sa.Table(
    "geo_denominators",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("metric_name", sa.Text, nullable=False),
    sa.Column("nuts_code", sa.Text),
    sa.Column("municipality_code", sa.Text),
    sa.Column("reference_year", sa.Integer, nullable=False),
    sa.Column("value", sa.Numeric(20, 2), nullable=False),
    sa.Column("external_dataset_id", UUID(as_uuid=True), sa.ForeignKey("external_datasets.id")),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

# administrative_boundaries.nuts_code is a soft reference to nuts_areas(code)
# — not modeled here, same "looked up by, not hard-FK'd from" convention as
# act_locations.nuts_code (nuts_areas itself isn't written by anything yet).
administrative_boundaries = sa.Table(
    "administrative_boundaries",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("boundary_type", sa.Text, nullable=False),
    sa.Column("external_dataset_id", UUID(as_uuid=True), sa.ForeignKey("external_datasets.id")),
    sa.Column("code", sa.Text),
    sa.Column("name", sa.Text),
    sa.Column("nuts_code", sa.Text),
    sa.Column("geom", Geometry(geometry_type="MULTIPOLYGON", srid=4326), nullable=False),
    sa.Column("valid_from", sa.TIMESTAMP(timezone=True)),
    sa.Column("valid_to", sa.TIMESTAMP(timezone=True)),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

facilities = sa.Table(
    "facilities",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("facility_type", sa.Text, nullable=False),
    sa.Column("external_dataset_id", UUID(as_uuid=True), sa.ForeignKey("external_datasets.id")),
    sa.Column("code", sa.Text),
    sa.Column("name", sa.Text),
    sa.Column("nuts_code", sa.Text),
    sa.Column("municipality_code", sa.Text),
    sa.Column("capacity_metric", sa.Text),
    sa.Column("capacity_value", sa.Numeric(12, 0)),
    sa.Column("geom", Geometry(geometry_type="POINT", srid=4326)),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

entities = sa.Table(
    "entities",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("canonical_name", sa.Text, nullable=False),
    sa.Column("normalized_name", sa.Text, nullable=False),
    sa.Column("country_code", sa.CHAR(2)),
    sa.Column("status", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("merged_into_id", UUID(as_uuid=True), sa.ForeignKey("entities.id")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

entity_identifiers = sa.Table(
    "entity_identifiers",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("scheme", sa.Text, nullable=False),
    sa.Column("value_raw", sa.Text, nullable=False),
    sa.Column("value_normalized", sa.Text, nullable=False),
    sa.Column("country_code", sa.CHAR(2)),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default="1"),
    sa.Column("identifier_valid", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("match_eligibility", sa.Text, nullable=False, server_default="ELIGIBLE"),
    sa.Column("valid_from", sa.TIMESTAMP(timezone=True)),
    sa.Column("valid_to", sa.TIMESTAMP(timezone=True)),
    sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

entity_names = sa.Table(
    "entity_names",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("normalized_name", sa.Text, nullable=False),
    sa.Column("name_search", sa.Text),
    sa.Column("name_without_legal_form", sa.Text),
    sa.Column("name_type", sa.Text, nullable=False),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("valid_from", sa.TIMESTAMP(timezone=True)),
    sa.Column("valid_to", sa.TIMESTAMP(timezone=True)),
    sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
)

entity_addresses = sa.Table(
    "entity_addresses",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("address_line", sa.Text),
    sa.Column("postal_code", sa.Text),
    sa.Column("municipality", sa.Text),
    sa.Column("region", sa.Text),
    sa.Column("nuts_code", sa.Text),
    sa.Column("country_code", sa.CHAR(2)),
    sa.Column("geom", Geometry(geometry_type="POINT", srid=4326)),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("valid_from", sa.TIMESTAMP(timezone=True)),
    sa.Column("valid_to", sa.TIMESTAMP(timezone=True)),
    sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
)

entity_match_candidates = sa.Table(
    "entity_match_candidates",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_a_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("entity_b_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("score", sa.Numeric(5, 4), nullable=False),
    sa.Column("score_breakdown", JSONB, nullable=False),
    sa.Column("blocking_reason", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="PENDING_REVIEW"),
    sa.Column("reviewed_by", UUID(as_uuid=True)),
    sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("review_notes", sa.Text),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

entity_merge_log = sa.Table(
    "entity_merge_log",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("surviving_entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("merged_entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("match_candidate_id", UUID(as_uuid=True), sa.ForeignKey("entity_match_candidates.id")),
    sa.Column("merge_reason", sa.Text, nullable=False),
    sa.Column("evidence", JSONB, nullable=False),
    sa.Column("performed_by", sa.Text, nullable=False),
    sa.Column("performed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("reverted_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("reverted_by", sa.Text),
)

entity_company_snapshots = sa.Table(
    "entity_company_snapshots",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("official_name", sa.Text),
    sa.Column("trade_name", sa.Text),
    sa.Column("gemi_number", sa.Text),
    sa.Column("vat_number", sa.Text),
    sa.Column("legal_form", sa.Text),
    sa.Column("legal_form_code", sa.Text),
    sa.Column("company_status", sa.Text),
    sa.Column("gemi_office", sa.Text),
    sa.Column("gemi_registration_date", sa.Date),
    sa.Column("kad_codes", sa.ARRAY(sa.Text)),
    sa.Column("municipality", sa.Text),
    sa.Column("region", sa.Text),
    sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("valid_to", sa.TIMESTAMP(timezone=True)),
    sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

entity_vies_checks = sa.Table(
    "entity_vies_checks",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("country_code", sa.CHAR(2), nullable=False),
    sa.Column("national_number", sa.Text, nullable=False),
    sa.Column("normalized_eu_vat", sa.Text, nullable=False),
    sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("vies_valid", sa.Boolean),
    sa.Column("vies_response_hash", sa.Text),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

procurement_processes = sa.Table(
    "procurement_processes",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("public_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("normalized_title", sa.Text),
    sa.Column("lifecycle_status", sa.Text, nullable=False, server_default="DISCOVERED"),
    sa.Column("record_status", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("merged_into_process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id")),
    sa.Column("buyer_entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id")),
    sa.Column("primary_cpv_code", sa.Text),
    sa.Column("estimated_value", sa.Numeric(20, 2)),
    sa.Column("awarded_value", sa.Numeric(20, 2)),
    sa.Column("current_contract_value", sa.Numeric(20, 2)),
    sa.Column("currency", sa.CHAR(3), server_default="EUR"),
    sa.Column("first_observed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_observed_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

procurement_acts = sa.Table(
    "procurement_acts",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id")),
    sa.Column("act_type", sa.Text, nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("normalized_title", sa.Text),
    sa.Column("publication_date", sa.Date),
    sa.Column("submission_date", sa.Date),
    sa.Column("submission_deadline", sa.TIMESTAMP(timezone=True)),
    sa.Column("decision_date", sa.Date),
    sa.Column("start_date", sa.Date),
    sa.Column("end_date", sa.Date),
    sa.Column("status", sa.Text),
    sa.Column("amount_net", sa.Numeric(20, 2)),
    sa.Column("vat_amount", sa.Numeric(20, 2)),
    sa.Column("amount_gross", sa.Numeric(20, 2)),
    sa.Column("currency", sa.CHAR(3), server_default="EUR"),
    sa.Column("procedure_type", sa.Text),
    sa.Column("agreement_type", sa.Text, nullable=False, server_default="STANDARD"),
    sa.Column("framework_ceiling_amount", sa.Numeric(20, 2)),
    sa.Column("source_details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id"), nullable=False),
    sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

act_identifiers = sa.Table(
    "act_identifiers",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("scheme", sa.Text, nullable=False),
    sa.Column("value_raw", sa.Text, nullable=False),
    sa.Column("value_normalized", sa.Text, nullable=False),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default="1"),
)

act_parties = sa.Table(
    "act_parties",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("party_role", sa.Text, nullable=False),
    sa.Column("lot_id", UUID(as_uuid=True)),  # FK -> procurement_lots, not defined in this slice
    sa.Column("amount", sa.Numeric(20, 2)),
    sa.Column("currency", sa.CHAR(3), server_default="EUR"),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

act_cpv_codes = sa.Table(
    "act_cpv_codes",
    metadata,
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), primary_key=True),
    sa.Column("cpv_code", sa.Text, primary_key=True),
    sa.Column("lot_id", UUID(as_uuid=True)),  # FK -> procurement_lots, not defined in this slice
    sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

act_locations = sa.Table(
    "act_locations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("nuts_code", sa.Text),
    sa.Column("municipality_code", sa.Text),
    sa.Column("postal_code", sa.Text),
    sa.Column("place_text", sa.Text),
    sa.Column("municipality_name", sa.Text),
    sa.Column("regional_unit_name", sa.Text),
    sa.Column("region_name", sa.Text),
    sa.Column("country_code", sa.CHAR(2)),
    sa.Column("location_kind", sa.Text, nullable=False, server_default="PERFORMANCE"),
    sa.Column("granularity", sa.Text),
    sa.Column("extraction_method", sa.Text),
    sa.Column("geocode_provider", sa.Text),
    sa.Column("confidence", sa.Numeric(5, 4)),
    sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("enrichment_job_id", UUID(as_uuid=True)),
    sa.Column("enriched_at", sa.TIMESTAMP(timezone=True)),
    # geom (PostGIS geometry) intentionally omitted, see module docstring.
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

geospatial_enrichment_jobs = sa.Table(
    "geospatial_enrichment_jobs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id"), nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="QUEUED"),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("available_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("locked_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("locked_by", sa.Text),
    sa.Column("last_error", JSONB),
    sa.Column("result", JSONB),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
)

geocoding_cache = sa.Table(
    "geocoding_cache",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("query_hash", sa.Text, nullable=False),
    sa.Column("query_normalized", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("latitude", sa.Float),
    sa.Column("longitude", sa.Float),
    sa.Column("display_name", sa.Text),
    sa.Column("municipality_name", sa.Text),
    sa.Column("regional_unit_name", sa.Text),
    sa.Column("region_name", sa.Text),
    sa.Column("postal_code", sa.Text),
    sa.Column("country_code", sa.CHAR(2)),
    sa.Column("precision", sa.Text),
    sa.Column("raw_response", JSONB),
    sa.Column("hit_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

geocoding_places = sa.Table(
    "geocoding_places",
    metadata,
    sa.Column("geoname_id", sa.BigInteger, primary_key=True),
    sa.Column("country_code", sa.CHAR(2), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("normalized_names", sa.ARRAY(sa.Text), nullable=False),
    sa.Column("admin_name_1", sa.Text),
    sa.Column("admin_code_1", sa.Text),
    sa.Column("admin_name_2", sa.Text),
    sa.Column("admin_code_2", sa.Text),
    sa.Column("admin_name_3", sa.Text),
    sa.Column("admin_code_3", sa.Text),
    sa.Column("latitude", sa.Float, nullable=False),
    sa.Column("longitude", sa.Float, nullable=False),
    sa.Column("feature_class", sa.CHAR(1), nullable=False),
    sa.Column("feature_code", sa.Text, nullable=False),
    sa.Column("population", sa.BigInteger),
    sa.Column("source_name", sa.Text, nullable=False),
    sa.Column("source_version", sa.Text, nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

process_participations = sa.Table(
    "process_participations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id"), nullable=False),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id")),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id")),
    sa.Column("participant_name_raw", sa.Text),
    sa.Column("participant_afm_raw", sa.Text),
    sa.Column("participation_role", sa.Text, nullable=False),
    sa.Column("evidence_type", sa.Text, nullable=False),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id")),
    sa.Column("source_page", sa.Integer),
    sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("evidence_key", sa.Text, nullable=False),
    sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

# documents/field_provenance mirror db/migrations/04_documents_and_provenance.sql
# (services/documents, spec §23/§24). document_pages mirrors the later
# 13_document_pages.sql — full-text search over per-page OCR/text-layer
# output, not modeled in 04_* because that migration predates page-level
# text storage being needed.
documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id")),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("document_type", sa.Text),
    sa.Column("title", sa.Text),
    sa.Column("source_url", sa.Text),
    sa.Column("object_uri", sa.Text, nullable=False),
    sa.Column("mime_type", sa.Text),
    sa.Column("file_size", sa.BigInteger),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("text_extraction_status", sa.Text, nullable=False, server_default="PENDING"),
    sa.Column("page_count", sa.Integer),
    sa.Column("language", sa.Text),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

field_provenance = sa.Table(
    "field_provenance",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column("object_id", UUID(as_uuid=True), nullable=False),
    sa.Column("field_name", sa.Text, nullable=False),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id"), nullable=False),
    sa.Column("source_path", sa.Text),
    sa.Column("extraction_method", sa.Text, nullable=False),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
    sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("value_hash", sa.Text),
)

document_pages = sa.Table(
    "document_pages",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
    sa.Column("page_number", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False, server_default=""),
    # text_search (generated tsvector column) intentionally omitted — never
    # written to from Python, only queried; same convention as act_locations.geom.
    sa.Column("extraction_method", sa.Text, nullable=False),
    sa.Column("ocr_mean_confidence", sa.Numeric(5, 2)),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

funding_projects = sa.Table(
    "funding_projects",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("mis_ops_code", sa.Text),
    sa.Column("program_code", sa.Text),
    sa.Column("program_period", sa.Text),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("beneficiary_id", UUID(as_uuid=True), sa.ForeignKey("entities.id")),
    sa.Column("budget", sa.Numeric(20, 2)),
    sa.Column("contracted_amount", sa.Numeric(20, 2)),
    sa.Column("paid_amount", sa.Numeric(20, 2)),
    sa.Column("currency", sa.CHAR(3), server_default="EUR"),
    sa.Column("start_date", sa.Date),
    sa.Column("end_date", sa.Date),
    sa.Column("status", sa.Text),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

funding_links = sa.Table(
    "funding_links",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("funding_project_id", UUID(as_uuid=True), sa.ForeignKey("funding_projects.id"), nullable=False),
    sa.Column("link_method", sa.Text, nullable=False),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
    sa.Column("evidence", JSONB, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    # NULL = not yet reviewed — the pending-review state for a Level 4
    # (§19.2) low-confidence match; mirrors act_links.reviewed_by.
    sa.Column("reviewed_by", UUID(as_uuid=True)),
)

ted_notice_details = sa.Table(
    "ted_notice_details",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False, unique=True),
    sa.Column("ted_notice_id", sa.Text, nullable=False),
    sa.Column("publication_number", sa.Text),
    sa.Column("raw_format", sa.Text, nullable=False),
    sa.Column("notice_type", sa.Text),
    sa.Column("eforms_version", sa.Text),
    sa.Column("parser_version", sa.Text, nullable=False),
    sa.Column("parse_confidence", sa.Numeric(5, 4), nullable=False, server_default="1"),
    sa.Column("buyer_raw", JSONB),
    sa.Column("supplier_raw", JSONB),
    sa.Column("lots", JSONB),
    sa.Column("country_code", sa.CHAR(2)),
    sa.Column("nuts_codes", sa.ARRAY(sa.Text)),
    sa.Column("related_notice_ids", sa.ARRAY(sa.Text)),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
)

mef_organizations = sa.Table(
    "mef_organizations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id")),
    sa.Column("source_native_id", sa.Text),
    sa.Column("name", sa.Text),
    sa.Column("afm_raw", sa.Text),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

mef_expenses = sa.Table(
    "mef_expenses",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("mef_organization_id", UUID(as_uuid=True), sa.ForeignKey("mef_organizations.id"), nullable=False),
    sa.Column("recipient_entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id")),
    sa.Column("recipient_afm_raw", sa.Text),
    sa.Column("amount", sa.Numeric(20, 2)),
    sa.Column("vat_amount", sa.Numeric(20, 2)),
    sa.Column("expense_date", sa.Date),
    sa.Column("related_ada_raw", sa.Text),
    sa.Column("linked_act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id")),
    sa.Column("link_method", sa.Text),
    sa.Column("confidence", sa.Numeric(5, 4)),
    sa.Column("source_record_id", UUID(as_uuid=True), sa.ForeignKey("source_records.id")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

act_links = sa.Table(
    "act_links",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("from_act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("to_act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("link_type", sa.Text, nullable=False),
    sa.Column("link_method", sa.Text, nullable=False),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
    sa.Column("evidence", JSONB, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("reviewed_by", UUID(as_uuid=True)),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

process_members = sa.Table(
    "process_members",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id"), nullable=False),
    sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("procurement_acts.id"), nullable=False),
    sa.Column("added_via", sa.Text, nullable=False),
    sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

process_merge_log = sa.Table(
    "process_merge_log",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("surviving_process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id"), nullable=False),
    sa.Column("merged_process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id"), nullable=False),
    sa.Column("merge_reason", sa.Text, nullable=False),
    sa.Column("evidence", JSONB, nullable=False),
    sa.Column("performed_by", sa.Text, nullable=False),
    sa.Column("performed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("reverted_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("reverted_by", sa.Text),
)

tenants = sa.Table(
    "tenants",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("plan", sa.Text, nullable=False, server_default="STARTER"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("display_name", sa.Text),
    sa.Column("is_internal_reviewer", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("mfa_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

tenant_memberships = sa.Table(
    "tenant_memberships",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

audit_log = sa.Table(
    "audit_log",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id")),
    sa.Column("actor_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id")),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("object_type", sa.Text),
    sa.Column("object_id", UUID(as_uuid=True)),
    sa.Column("details", JSONB),
    sa.Column("ip_address", sa.Text),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

saved_searches = sa.Table(
    "saved_searches",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("query", JSONB, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

business_profiles = sa.Table(
    "business_profiles",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
    sa.Column("company_name", sa.Text),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("cpv_prefixes", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("keywords", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("nuts_codes", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("municipality", sa.Text),
    sa.Column("buyer_types", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("procedure_types", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("amount_min", sa.Numeric(18, 2)),
    sa.Column("amount_max", sa.Numeric(18, 2)),
    sa.Column("classification_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("classified_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

business_profile_terms = sa.Table(
    "business_profile_terms",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey("business_profiles.id"), nullable=False),
    sa.Column("term_type", sa.Text, nullable=False),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False, server_default="RULE"),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

notes = sa.Table(
    "notes",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column("object_id", UUID(as_uuid=True), nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

tags = sa.Table(
    "tags",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

tag_links = sa.Table(
    "tag_links",
    metadata,
    sa.Column("tag_id", UUID(as_uuid=True), sa.ForeignKey("tags.id"), primary_key=True),
    sa.Column("object_type", sa.Text, primary_key=True),
    sa.Column("object_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

workspace_watch_items = sa.Table(
    "workspace_watch_items",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column("object_id", UUID(as_uuid=True), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

opportunity_pipeline_items = sa.Table(
    "opportunity_pipeline_items",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id"), nullable=False),
    sa.Column("stage", sa.Text, nullable=False, server_default="WATCHING"),
    sa.Column("assigned_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id")),
    sa.Column("priority", sa.Text, nullable=False, server_default="MEDIUM"),
    sa.Column("expected_value", sa.Numeric(18, 2)),
    sa.Column("next_action", sa.Text),
    sa.Column("due_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

opportunity_pipeline_history = sa.Table(
    "opportunity_pipeline_history",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("pipeline_item_id", UUID(as_uuid=True), sa.ForeignKey("opportunity_pipeline_items.id"), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("from_stage", sa.Text),
    sa.Column("to_stage", sa.Text, nullable=False),
    sa.Column("changed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("changed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

alert_rules = sa.Table(
    "alert_rules",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("event_types", sa.ARRAY(sa.Text), nullable=False),
    sa.Column("filters", JSONB, nullable=False),
    sa.Column("schedule", sa.Text, nullable=False),
    sa.Column("delivery_channels", sa.ARRAY(sa.Text), nullable=False),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("last_evaluated_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("timezone", sa.Text, nullable=False, server_default="Europe/Athens"),
    sa.Column("digest_time", sa.Time, nullable=False, server_default="08:00"),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

opportunity_scores = sa.Table(
    "opportunity_scores",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("process_id", UUID(as_uuid=True), sa.ForeignKey("procurement_processes.id"), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("total_score", sa.Numeric(5, 2), nullable=False),
    sa.Column("cpv_company_fit_score", sa.Numeric(5, 2), nullable=False),
    sa.Column("buyer_affinity_score", sa.Numeric(5, 2), nullable=False),
    sa.Column("timing_score", sa.Numeric(5, 2), nullable=False),
    sa.Column("competitive_attractiveness_score", sa.Numeric(5, 2), nullable=False),
    sa.Column("contract_value_fit_score", sa.Numeric(5, 2), nullable=False),
    sa.Column("data_confidence_score", sa.Numeric(5, 2), nullable=False),
    sa.Column("evidence", JSONB, nullable=False),
    sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

webhook_deliveries = sa.Table(
    "webhook_deliveries",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("alert_event_id", UUID(as_uuid=True), sa.ForeignKey("alert_events.id"), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("endpoint_url", sa.Text, nullable=False),
    sa.Column("idempotency_key", sa.Text, nullable=False),
    sa.Column("signature", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="PENDING"),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("next_retry_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("response_status", sa.Integer),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

mart_refresh_state = sa.Table(
    "mart_refresh_state",
    metadata,
    sa.Column("mart_name", sa.Text, primary_key=True),
    sa.Column("last_refresh_started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_refresh_finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_error", JSONB),
)

alert_delivery_targets = sa.Table(
    "alert_delivery_targets",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("alert_rule_id", UUID(as_uuid=True), sa.ForeignKey("alert_rules.id"), nullable=False),
    sa.Column("channel_type", sa.Text, nullable=False),
    sa.Column("target", sa.Text, nullable=False),
    sa.Column("secret", sa.Text),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
)

alert_events = sa.Table(
    "alert_events",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("alert_rule_id", UUID(as_uuid=True), sa.ForeignKey("alert_rules.id"), nullable=False),
    sa.Column("canonical_object_type", sa.Text, nullable=False),
    sa.Column("canonical_object_id", UUID(as_uuid=True), nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("material_change_hash", sa.Text, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("triggered_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("delivered_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("read_at", sa.TIMESTAMP(timezone=True)),
)

alert_digest_runs = sa.Table(
    "alert_digest_runs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("alert_rule_id", UUID(as_uuid=True), sa.ForeignKey("alert_rules.id")),
    sa.Column("schedule", sa.Text, nullable=False),
    sa.Column("period_key", sa.Text, nullable=False),
    sa.Column("period_started_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("period_ended_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("event_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("status", sa.Text, nullable=False, server_default="PENDING"),
    sa.Column("channels", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("error", JSONB),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("delivered_at", sa.TIMESTAMP(timezone=True)),
)

export_jobs = sa.Table(
    "export_jobs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("export_type", sa.Text, nullable=False),
    sa.Column("format", sa.Text, nullable=False),
    sa.Column("filters", JSONB, nullable=False, server_default="{}"),
    sa.Column("status", sa.Text, nullable=False, server_default="PENDING"),
    sa.Column("row_count", sa.Integer),
    sa.Column("file_name", sa.Text),
    sa.Column("mime_type", sa.Text),
    sa.Column("storage_path", sa.Text),
    sa.Column("error", JSONB),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
)

opportunity_score_jobs = sa.Table(
    "opportunity_score_jobs",
    metadata,
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), primary_key=True),
    sa.Column("status", sa.Text, nullable=False, server_default="QUEUED"),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("requested_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("error", JSONB),
)
