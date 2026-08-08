"""Default daily jobs for primary feeds and their enrichment providers.

Both sources have confirmed production defaults and are registered without
environment overrides. Διαύγεια, ΓΕΜΗ, ΑΝΑΠΤΥΞΗ and ΜΕΦ are targeted
enrichment hooks in the ΚΗΜΔΗΣ flow rather than unrelated whole-site
downloads. VIES similarly enriches foreign suppliers found through TED.

CKAN is deliberately **not** a `ScheduledJob` here — it's a whole-dataset
refresh ("redownload this file"), not a date-windowed backfill, so it
doesn't fit this module's abstraction. See
`services/ingestion/connectors/ckan/scheduled.py::refresh_due_ckan_datasets`,
wired directly into `orchestration/cli.py::_run_once` alongside
`run_due_jobs` rather than through `default_jobs()`.
"""

from __future__ import annotations

import os
import math
from datetime import date, timedelta

from services.ingestion.connectors.anaptyxi.config import (
    SUPPORTED_PROGRAM_PERIODS,
    AnaptyxiConnectorConfig,
)
from services.ingestion.connectors.diavgeia.config import DiavgeiaConnectorConfig
from services.ingestion.connectors.gemi.config import GemiConnectorConfig
from services.ingestion.connectors.khmdhs.config import KhmdhsConnectorConfig
from services.ingestion.connectors.khmdhs.scheduled import run_scheduled_window
from services.ingestion.connectors.mef.config import MefConnectorConfig
from services.ingestion.connectors.ted.config import TedConnectorConfig
from services.ingestion.connectors.ted.scheduled import run_scheduled_window as run_ted_scheduled_window
from services.ingestion.connectors.vies.config import ViesConnectorConfig
from services.intelligence.eu_matching import EU_MEMBER_COUNTRIES, countries_for_day
from services.search_index.config import OpenSearchConfig

from .scheduler import ScheduledJob

DEFAULT_BACKFILL_START_DATE = date(2024, 1, 1)
DEFAULT_DAILY_LOOKBACK_DAYS = 3
DEFAULT_DAILY_MIN_INTERVAL_HOURS = 23.0
DEFAULT_DAILY_GEMI_MAX_LOOKUPS = 4000


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


def default_jobs(*, raw_root: str = "./raw") -> tuple[list[ScheduledJob], list[str]]:
    """Returns (jobs, skip_reasons) — jobs whose config isn't present are
    left out of the first list and explained in the second, rather than
    raising, so `cli.py run-once`/`run-forever` can start with a partial
    set of sources configured."""
    jobs: list[ScheduledJob] = []
    skip_reasons: list[str] = []
    lookback_days = _positive_int_env("DAILY_INGEST_LOOKBACK_DAYS", DEFAULT_DAILY_LOOKBACK_DAYS)
    min_interval = timedelta(
        hours=_positive_float_env("DAILY_INGEST_MIN_INTERVAL_HOURS", DEFAULT_DAILY_MIN_INTERVAL_HOURS)
    )
    diavgeia_budget = _positive_int_env("DAILY_DIAVGEIA_MAX_LOOKUPS", 1000)
    gemi_budget = _positive_int_env(
        "DAILY_GEMI_MAX_LOOKUPS", DEFAULT_DAILY_GEMI_MAX_LOOKUPS
    )
    mef_budget = _positive_int_env("DAILY_MEF_MAX_LOOKUPS", 500)
    anaptyxi_budget = _positive_int_env("DAILY_ANAPTYXI_MAX_LOOKUPS_PER_PERIOD", 500)
    vies_budget = _positive_int_env("DAILY_VIES_MAX_LOOKUPS", 500)
    document_budget = _positive_int_env("DAILY_DOCUMENT_MAX_DOWNLOADS", 10000)
    adamchain_budget = _positive_int_env("DAILY_ADAMCHAIN_MAX_LOOKUPS", 12000)

    # Optional cross-cutting enrichment, not a job of its own — if
    # OPENSEARCH_URL isn't set, ΚΗΜΔΗΣ ingestion just runs without
    # incremental indexing (not a reason to skip the ΚΗΜΔΗΣ job itself).
    try:
        opensearch_config: OpenSearchConfig | None = OpenSearchConfig.from_env()
    except RuntimeError:
        opensearch_config = None

    try:
        diavgeia_config: DiavgeiaConnectorConfig | None = DiavgeiaConnectorConfig.from_env()
    except RuntimeError as exc:
        diavgeia_config = None
        skip_reasons.append(f"DIAVGEIA enrichment inactive: {exc}")

    try:
        gemi_config: GemiConnectorConfig | None = GemiConnectorConfig.from_env()
    except RuntimeError as exc:
        gemi_config = None
        skip_reasons.append(f"GEMI enrichment inactive: {exc}")

    anaptyxi_configs: list[AnaptyxiConnectorConfig] = []
    for program_period in SUPPORTED_PROGRAM_PERIODS:
        try:
            anaptyxi_configs.append(AnaptyxiConnectorConfig.from_env(program_period=program_period))
        except RuntimeError as exc:
            skip_reasons.append(f"{program_period} enrichment inactive: {exc}")

    try:
        mef_config: MefConnectorConfig | None = MefConnectorConfig.from_env()
    except RuntimeError as exc:
        mef_config = None
        skip_reasons.append(f"MEF enrichment inactive: {exc}")

    try:
        vies_config: ViesConnectorConfig | None = ViesConnectorConfig.from_env()
    except RuntimeError as exc:
        vies_config = None
        skip_reasons.append(f"VIES enrichment inactive: {exc}")

    try:
        KhmdhsConnectorConfig.from_env()
    except RuntimeError as exc:
        skip_reasons.append(f"KHMDHS job skipped: {exc}")
    else:
        jobs.append(
            ScheduledJob(
                source_system="KHMDHS",
                resource_type="ALL",
                partition_key="GLOBAL",
                window_days=30,
                backfill_start_date=DEFAULT_BACKFILL_START_DATE,
                min_interval=min_interval,
                rolling_lookback_days=lookback_days,
                run_window=lambda conn, date_from, date_to: run_scheduled_window(
                    conn,
                    date_from,
                    date_to,
                    raw_root=raw_root,
                    opensearch_config=opensearch_config,
                    diavgeia_config=diavgeia_config,
                    diavgeia_search=False,
                    gemi_config=gemi_config,
                    anaptyxi_configs=tuple(anaptyxi_configs),
                    mef_config=mef_config,
                    process_documents=True,
                    inline_enrichment_providers={
                        "ALERTS",
                        "OPENSEARCH",
                    },
                    queue_unconfigured_providers=True,
                    provider_lookup_budgets={
                        "KHMDHS_ADAMCHAIN": adamchain_budget,
                        "DIAVGEIA": diavgeia_budget,
                        "DIAVGEIA_SEARCH": diavgeia_budget,
                        "GEMI": gemi_budget,
                        "MEF": mef_budget,
                        "KHMDHS_DOCUMENT": document_budget,
                        **{period: anaptyxi_budget for period in SUPPORTED_PROGRAM_PERIODS},
                    },
                ),
            )
        )

    try:
        TedConnectorConfig.from_env()
    except RuntimeError as exc:
        skip_reasons.append(f"TED job skipped: {exc}")
    else:
        configured_countries = tuple(
            dict.fromkeys(
                code.strip().upper()
                for code in os.environ.get(
                    "TED_BENCHMARK_COUNTRIES",
                    ",".join(EU_MEMBER_COUNTRIES),
                ).split(",")
                if code.strip()
            )
        )
        country_batch_size = _positive_int_env("TED_COUNTRIES_PER_CYCLE", 5)
        scheduled_countries = countries_for_day(
            date.today(),
            countries=configured_countries,
            batch_size=min(country_batch_size, len(configured_countries)),
        )
        rotating_slots = max(country_batch_size - (1 if "GR" in configured_countries else 0), 1)
        rotation_days = math.ceil(
            max(len(configured_countries) - (1 if "GR" in configured_countries else 0), 0)
            / rotating_slots
        )
        ted_lookback_days = _positive_int_env(
            "TED_DAILY_LOOKBACK_DAYS",
            max(lookback_days, rotation_days + 2),
        )
        for country in scheduled_countries:
            jobs.append(
                ScheduledJob(
                    source_system="TED",
                    resource_type="ALL",
                    partition_key=country,
                    window_days=30,
                    backfill_start_date=DEFAULT_BACKFILL_START_DATE,
                    min_interval=min_interval,
                    rolling_lookback_days=ted_lookback_days,
                    run_window=lambda conn, date_from, date_to, country=country: run_ted_scheduled_window(
                        conn,
                        date_from,
                        date_to,
                        country=country,
                        raw_root=raw_root,
                        vies_config=vies_config,
                        vies_lookup_budget=vies_budget,
                        opensearch_config=opensearch_config,
                    ),
                )
            )

    return jobs, skip_reasons
