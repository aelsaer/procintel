from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from packages.domain.tables import spatial_service_capabilities
from services.ingestion.connectors.inspire.capabilities import (
    _capability_source_record_insert,
    capability_health,
    capability_quality_issue,
    parse_wfs_capabilities,
    parse_wms_capabilities,
)
from services.ingestion.connectors.inspire.csw import (
    DiscoveredSpatialService,
    discover_spatial_services,
    parse_csw_records,
    select_services_for_check,
)
from services.ingestion.connectors.inspire.postal import (
    parse_postal_nuts_archive,
)
from services.ingestion.connectors.inspire.selected_layers import (
    SELECTED_INSPIRE_LAYERS,
    get_selected_layer,
    normalize_wms_bbox,
    wms_get_map_params,
)


def test_parse_wms_capabilities_preserves_service_contract() -> None:
    payload = b"""<?xml version="1.0"?>
    <WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
      <Service>
        <Title>Greek reference service</Title>
        <Fees>none</Fees>
        <AccessConstraints>CC-BY</AccessConstraints>
      </Service>
      <Capability>
        <Request><GetMap><Format>image/png</Format></GetMap></Request>
        <Layer>
          <Title>Root</Title>
          <Layer><Name>regions</Name><Title>Regions</Title></Layer>
        </Layer>
      </Capability>
    </WMS_Capabilities>"""

    result = parse_wms_capabilities(payload)

    assert result["version"] == "1.3.0"
    assert result["title"] == "Greek reference service"
    assert result["access_constraints"] == "CC-BY"
    assert result["formats"] == ["image/png"]
    assert result["layers"] == [{"name": "regions", "title": "Regions"}]


def test_parse_wfs_capabilities_preserves_feature_types() -> None:
    payload = b"""<?xml version="1.0"?>
    <wfs:WFS_Capabilities version="2.0.0"
      xmlns:wfs="http://www.opengis.net/wfs/2.0"
      xmlns:ows="http://www.opengis.net/ows/1.1">
      <ows:ServiceIdentification>
        <ows:Title>Greek feature service</ows:Title>
        <ows:Fees>none</ows:Fees>
        <ows:AccessConstraints>CC-BY-4.0</ows:AccessConstraints>
      </ows:ServiceIdentification>
      <ows:OperationsMetadata>
        <ows:Parameter name="outputFormat"><ows:AllowedValues>
          <ows:Value>application/json</ows:Value>
        </ows:AllowedValues></ows:Parameter>
      </ows:OperationsMetadata>
      <wfs:FeatureTypeList>
        <wfs:FeatureType>
          <wfs:Name>ps:natura</wfs:Name><wfs:Title>Natura 2000</wfs:Title>
        </wfs:FeatureType>
      </wfs:FeatureTypeList>
    </wfs:WFS_Capabilities>"""

    result = parse_wfs_capabilities(payload)

    assert result["version"] == "2.0.0"
    assert result["title"] == "Greek feature service"
    assert result["access_constraints"] == "CC-BY-4.0"
    assert "application/json" in result["formats"]
    assert result["layers"] == [{"name": "ps:natura", "title": "Natura 2000"}]


def test_capability_health_does_not_claim_empty_service_is_available() -> None:
    assert capability_health({"layers": []}) == (
        "DEGRADED",
        "capabilities advertise no queryable layers",
    )
    assert capability_health({"layers": [{"name": "regions"}]}) == ("AVAILABLE", None)
    assert capability_quality_issue("DEGRADED") == (
        "OGC_CAPABILITIES_DEGRADED",
        "WARNING",
    )
    assert capability_quality_issue("AVAILABLE") is None


def test_capability_raw_record_insert_is_content_hash_idempotent() -> None:
    statement = _capability_source_record_insert(
        {
            "id": uuid.uuid4(),
            "source_system": "INSPIRE",
            "resource_type": "WMS_CAPABILITIES",
            "source_native_id": "https://example.test/wms",
            "content_sha256": "a" * 64,
            "payload_uri": "mem://capabilities",
            "fetched_at": datetime.now(timezone.utc),
        }
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert (
        "ON CONFLICT (source_system, resource_type, content_sha256) DO NOTHING"
        in sql
    )


def test_selected_wms_request_is_allowlisted_and_parameter_bounded() -> None:
    layer = get_selected_layer("flood-hazard-high")
    assert layer is not None
    params = wms_get_map_params(
        layer,
        bbox="2100000,4300000,3200000,5300000",
        width=256,
        height=256,
        srs="EPSG:3857",
    )

    assert params["LAYERS"] == "NZ.Flood"
    assert params["FORMAT"] == "image/png"
    assert params["TRANSPARENT"] == "true"
    assert len(SELECTED_INSPIRE_LAYERS) == 3
    assert get_selected_layer("not-allowlisted") is None


@pytest.mark.parametrize(
    "bbox",
    ("1,2,3", "nan,2,3,4", "4,2,3,5", "-90000000,0,1,2"),
)
def test_selected_wms_bbox_rejects_malformed_or_unsafe_values(bbox: str) -> None:
    with pytest.raises(ValueError):
        normalize_wms_bbox(bbox)


def test_csw_discovery_extracts_and_deduplicates_wms_wfs_references() -> None:
    payload = """<?xml version="1.0"?>
    <csw:GetRecordsResponse
      xmlns:csw="http://www.opengis.net/cat/csw/2.0.2"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:dct="http://purl.org/dc/terms/">
      <csw:SearchResults nextRecord="0">
        <csw:Record>
          <dc:identifier>natura-2000</dc:identifier>
          <dc:title>Natura 2000 WMS and WFS</dc:title>
          <dc:publisher>ΥΠΕΝ</dc:publisher>
          <dct:modified>2026-01-12</dct:modified>
          <dc:rights>CC-BY-4.0</dc:rights>
          <dct:references>https://geoportal.ypen.gr/geoserver/natura/ows?service=WMS</dct:references>
          <dct:references>https://geoportal.ypen.gr/geoserver/natura/ows?service=WMS</dct:references>
          <dct:references>https://geoportal.ypen.gr/geoserver/natura/ows?SERVICE=WFS</dct:references>
          <dct:references>https://example.test/metadata</dct:references>
        </csw:Record>
        <csw:Record>
          <dc:identifier>cadastre</dc:identifier>
          <dc:title>Ελληνικό Κτηματολόγιο cadastral WMS</dc:title>
          <dct:references>https://gis.ktimanet.gr/inspire/service</dct:references>
        </csw:Record>
      </csw:SearchResults>
    </csw:GetRecordsResponse>""".encode()

    records = parse_csw_records(payload)
    services = discover_spatial_services(records)

    assert len(records) == 2
    assert len(services) == 3
    assert services[0].record_id == "natura-2000"
    assert services[0].service_type == "WMS"
    assert services[0].service_url == "https://geoportal.ypen.gr/geoserver/natura/ows"
    assert services[0].catalog_source == "GREEK_INSPIRE_CSW"
    assert services[1].service_type == "WFS"
    assert services[2].catalog_source == "KTIMATOLOGIO_INSPIRE"


def test_csw_service_budget_prioritizes_unseen_then_oldest() -> None:
    now = datetime.now(timezone.utc)

    def service(name: str) -> DiscoveredSpatialService:
        return DiscoveredSpatialService(
            record_id=name,
            title=name,
            publisher="ΥΠΕΝ",
            modified=None,
            license_code=None,
            service_type="WMS",
            service_url=f"https://example.test/{name}",
            catalog_source="GREEK_INSPIRE_CSW",
        )

    recent = service("recent")
    unseen = service("unseen")
    old = service("old")

    selected = select_services_for_check(
        [recent, unseen, old],
        last_checked={
            (recent.service_url, recent.service_type): now,
            (old.service_url, old.service_type): now - timedelta(days=20),
        },
        limit=2,
    )

    assert selected == [unseen, old]


def test_spatial_capability_identity_includes_service_type() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in spatial_service_capabilities.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }

    assert ("service_url", "service_type") in unique_columns


def _postal_archive(text: str, *, filename: str = "postal.csv") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(filename, text)
    return output.getvalue()


def test_parse_postal_nuts_archive_handles_official_tercet_quoting() -> None:
    payload = _postal_archive(
        "\ufeffNUTS3;CODE\n"
        "'EL303';'11142'\n"
        "'EL303';'11142'\n"
        "'EL642';'34016'\n"
    )

    assert parse_postal_nuts_archive(payload) == [
        ("11142", "EL303"),
        ("34016", "EL642"),
    ]


def test_parse_postal_nuts_archive_rejects_unusable_payload() -> None:
    with pytest.raises(ValueError, match="valid Greek mappings"):
        parse_postal_nuts_archive(_postal_archive("NUTS3;CODE\nXX000;invalid\n"))
