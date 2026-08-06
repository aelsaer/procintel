"""Official Ktimatologio and Eurostat GISCO reference endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_KTIMATOLOGIO_WMS_URL = (
    "https://gis.ktimanet.gr/inspire/rest/services/cadastralparcels/"
    "CadastralParcelWMS/MapServer/exts/InspireView/service"
)
DEFAULT_GREEK_INSPIRE_CSW_URL = (
    "http://geoportal.ypen.gr/geonetwork/srv/eng/csw"
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
    greek_inspire_csw_url: str = DEFAULT_GREEK_INSPIRE_CSW_URL
    greece_nuts_url: str = DEFAULT_GREECE_NUTS_URL
    greece_postal_nuts_url: str = DEFAULT_GREECE_POSTAL_NUTS_URL
    request_timeout_seconds: float = 60.0
    csw_max_records: int = 200
    csw_max_service_checks: int = 40
    blocked_retry_days: int = 30

    @classmethod
    def from_env(cls) -> "InspireReferenceConfig":
        return cls(
            ktimatologio_wms_url=os.environ.get(
                "INSPIRE_KTIMATOLOGIO_WMS_URL"
            ) or DEFAULT_KTIMATOLOGIO_WMS_URL,
            greek_inspire_csw_url=os.environ.get(
                "INSPIRE_GREEK_CSW_URL"
            ) or DEFAULT_GREEK_INSPIRE_CSW_URL,
            greece_nuts_url=os.environ.get(
                "INSPIRE_GREECE_NUTS_URL"
            ) or DEFAULT_GREECE_NUTS_URL,
            greece_postal_nuts_url=os.environ.get(
                "INSPIRE_GREECE_POSTAL_NUTS_URL"
            ) or DEFAULT_GREECE_POSTAL_NUTS_URL,
            request_timeout_seconds=_positive_float_env(
                "INSPIRE_REQUEST_TIMEOUT_SECONDS",
                cls.request_timeout_seconds,
            ),
            csw_max_records=_positive_int_env(
                "INSPIRE_CSW_MAX_RECORDS",
                cls.csw_max_records,
            ),
            csw_max_service_checks=_positive_int_env(
                "INSPIRE_CSW_MAX_SERVICE_CHECKS",
                cls.csw_max_service_checks,
            ),
            blocked_retry_days=_positive_int_env(
                "INSPIRE_BLOCKED_RETRY_DAYS",
                cls.blocked_retry_days,
            ),
        )


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value
