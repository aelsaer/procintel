"""Official Ktimatologio and Eurostat GISCO reference endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_KTIMATOLOGIO_WMS_URL = (
    "https://gis.ktimanet.gr/inspire/rest/services/cadastralparcels/"
    "CadastralParcelWMS/MapServer/exts/InspireView/service"
)
DEFAULT_GREECE_NUTS_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_03M_2024_4326.geojson"
)
DEFAULT_GREECE_POSTAL_NUTS_URL = (
    "https://gisco-services.ec.europa.eu/tercet/NUTS-2024/"
    "pc2025_EL_NUTS-2024_v1.0.zip"
)


@dataclass(frozen=True)
class InspireReferenceConfig:
    ktimatologio_wms_url: str = DEFAULT_KTIMATOLOGIO_WMS_URL
    greece_nuts_url: str = DEFAULT_GREECE_NUTS_URL
    greece_postal_nuts_url: str = DEFAULT_GREECE_POSTAL_NUTS_URL
    request_timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "InspireReferenceConfig":
        return cls(
            ktimatologio_wms_url=os.environ.get(
                "INSPIRE_KTIMATOLOGIO_WMS_URL",
                DEFAULT_KTIMATOLOGIO_WMS_URL,
            ),
            greece_nuts_url=os.environ.get(
                "INSPIRE_GREECE_NUTS_URL",
                DEFAULT_GREECE_NUTS_URL,
            ),
            greece_postal_nuts_url=os.environ.get(
                "INSPIRE_GREECE_POSTAL_NUTS_URL",
                DEFAULT_GREECE_POSTAL_NUTS_URL,
            ),
        )
