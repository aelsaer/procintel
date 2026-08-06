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


def test_blank_public_provider_overrides_use_defaults(monkeypatch):
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

    assert KhmdhsConnectorConfig.from_env().base_url == DEFAULT_KHMDHS_API_BASE_URL
    assert DiavgeiaConnectorConfig.from_env().base_url == DEFAULT_DIAVGEIA_API_BASE_URL
    assert MefConnectorConfig.from_env().base_url == DEFAULT_MEF_API_BASE_URL
    assert TedConnectorConfig.from_env().base_url == DEFAULT_TED_API_BASE_URL

    inspire = InspireReferenceConfig.from_env()
    assert inspire.ktimatologio_wms_url == DEFAULT_KTIMATOLOGIO_WMS_URL
    assert inspire.greek_inspire_csw_url == DEFAULT_GREEK_INSPIRE_CSW_URL
    assert inspire.greece_nuts_url == DEFAULT_GREECE_NUTS_URL
    assert inspire.greece_postal_nuts_url == DEFAULT_GREECE_POSTAL_NUTS_URL
