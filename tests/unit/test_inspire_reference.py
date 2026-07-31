from __future__ import annotations

import io
import zipfile

import pytest

from services.ingestion.connectors.inspire.capabilities import (
    parse_wms_capabilities,
)
from services.ingestion.connectors.inspire.postal import (
    parse_postal_nuts_archive,
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
