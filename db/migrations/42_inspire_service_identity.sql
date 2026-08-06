-- One OGC /ows endpoint may expose both WMS and WFS capabilities. Preserve
-- them as distinct service contracts while retaining URL-level discovery.

ALTER TABLE spatial_service_capabilities
    DROP CONSTRAINT IF EXISTS spatial_service_capabilities_service_url_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_spatial_capability_url_type
    ON spatial_service_capabilities (service_url, service_type);
