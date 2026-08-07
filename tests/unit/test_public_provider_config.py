from datetime import datetime, timezone

from services.ingestion.connectors.anaptyxi.config import AnaptyxiConnectorConfig
from services.ingestion.connectors.ckan.config import CkanConnectorConfig
from services.ingestion.connectors.diavgeia.config import (
    DEFAULT_DIAVGEIA_API_BASE_URL,
    DiavgeiaConnectorConfig,
)
from services.ingestion.connectors.inspire.config import (
    DEFAULT_GREECE_NUTS_URL,
    DEFAULT_GREECE_POSTAL_NUTS_URL,
    DEFAULT_GREEK_INSPIRE_CSW_URL,
    DEFAULT_KTIMATOLOGIO_WMS_URL,
    InspireReferenceConfig,
)
from services.ingestion.connectors.khmdhs.config import (
    DEFAULT_KHMDHS_API_BASE_URL,
    KhmdhsConnectorConfig,
)
from services.ingestion.connectors.mef.config import (
    DEFAULT_MEF_API_BASE_URL,
    MefConnectorConfig,
)
from services.ingestion.connectors.ted.config import (
    DEFAULT_TED_API_BASE_URL,
    TedConnectorConfig,
)
from services.ingestion.connectors.vies.config import ViesConnectorConfig


def test_blank_public_provider_overrides_use_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("RAW_STORE_ROOT", str(tmp_path))
    for name in (
        "KHMDHS_API_BASE_URL",
        "DIAVGEIA_API_BASE_URL",
        "MEF_API_BASE_URL",
        "TED_API_BASE_URL",
        "INSPIRE_KTIMATOLOGIO_WMS_URL",
        "INSPIRE_GREEK_CSW_URL",
        "INSPIRE_GREECE_NUTS_URL",
        "INSPIRE_GREECE_POSTAL_NUTS_URL",
    ):
        monkeypatch.setenv(name, "")

    khmdhs = KhmdhsConnectorConfig.from_env()
    diavgeia = DiavgeiaConnectorConfig.from_env()
    assert khmdhs.base_url == DEFAULT_KHMDHS_API_BASE_URL
    assert khmdhs.rate_limit_state_path == str(
        tmp_path / "provider-limits" / "khmdhs.json"
    )
    assert diavgeia.base_url == DEFAULT_DIAVGEIA_API_BASE_URL
    assert diavgeia.rate_limit_state_path == str(
        tmp_path / "provider-limits" / "diavgeia.json"
    )
    assert MefConnectorConfig.from_env().base_url == DEFAULT_MEF_API_BASE_URL
    mef = MefConnectorConfig.from_env()
    ted = TedConnectorConfig.from_env()
    vies = ViesConnectorConfig.from_env()
    ckan = CkanConnectorConfig.from_env()
    anaptyxi = AnaptyxiConnectorConfig.from_env("ANAPTYXI_2014_2020")
    assert mef.rate_limit_state_path == str(
        tmp_path / "provider-limits" / "mef.json"
    )
    current_year = datetime.now(timezone.utc).year
    assert mef.lookup_years == (current_year, current_year - 1, current_year - 2)
    assert ted.base_url == DEFAULT_TED_API_BASE_URL
    assert ted.rate_limit_state_path == str(
        tmp_path / "provider-limits" / "ted.json"
    )
    assert vies.rate_limit_state_path == str(
        tmp_path / "provider-limits" / "vies.json"
    )
    assert ckan.rate_limit_state_path == str(
        tmp_path / "provider-limits" / "ckan.json"
    )
    assert anaptyxi.rate_limit_state_path == str(
        tmp_path / "provider-limits" / "anaptyxi_2014_2020.json"
    )

    inspire = InspireReferenceConfig.from_env()
    assert inspire.ktimatologio_wms_url == DEFAULT_KTIMATOLOGIO_WMS_URL
    assert inspire.greek_inspire_csw_url == DEFAULT_GREEK_INSPIRE_CSW_URL
    assert inspire.greece_nuts_url == DEFAULT_GREECE_NUTS_URL
    assert inspire.greece_postal_nuts_url == DEFAULT_GREECE_POSTAL_NUTS_URL
