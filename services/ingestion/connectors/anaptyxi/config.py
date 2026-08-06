"""Connector configuration.

§19.3 requires separate source adapters per programming period —
`ANAPTYXI_2007_2013`, `ANAPTYXI_2014_2020`, `ANAPTYXI_2021_2027` — even
though they all converge on the same canonical `funding_projects` schema
(same principle the ΚΗΜΔΗΣ connector uses for its five resources: a
parameter, not a schema fork). In practice each period is very likely a
genuinely different deployment/hostname, not just a query parameter on one
system, so each period gets its **own** base-URL env var rather than
sharing one — `from_env(program_period=...)` picks the right one.

`ANAPTYXI_API_BASE_URL` (no period suffix) is kept as a backward-compatible
alias for `ANAPTYXI_2014_2020_API_BASE_URL` — 2014-2020 was the only period
built and documented before the other two, and existing deployments/docs
already reference the unsuffixed name.

The two legacy deployments have stable, publicly documented hosts and use
the project-level ``GetData.ashx`` contract validated by the connector
tests. They therefore have public defaults. The 2021-2027 portal currently
publishes aggregate chart endpoints only; it intentionally has no default
project API URL so the platform cannot present aggregate data as linked
project detail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_PROGRAM_PERIOD = "ANAPTYXI_2014_2020"

SUPPORTED_PROGRAM_PERIODS = ("ANAPTYXI_2007_2013", "ANAPTYXI_2014_2020", "ANAPTYXI_2021_2027")

_ENV_VAR_BY_PERIOD = {
    "ANAPTYXI_2007_2013": "ANAPTYXI_2007_2013_API_BASE_URL",
    "ANAPTYXI_2014_2020": "ANAPTYXI_2014_2020_API_BASE_URL",
    "ANAPTYXI_2021_2027": "ANAPTYXI_2021_2027_API_BASE_URL",
}

PUBLIC_API_BASE_URL_BY_PERIOD = {
    "ANAPTYXI_2007_2013": "https://2013.anaptyxi.gov.gr",
    "ANAPTYXI_2014_2020": "https://anaptyxi.gov.gr",
}


class AnaptyxiUpstreamContractError(RuntimeError):
    """The official portal exists but has no validated project API."""


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class AnaptyxiConnectorConfig:
    base_url: str
    program_period: str = DEFAULT_PROGRAM_PERIOD
    rate_limit_per_minute: float = 60.0  # no official number published; conservative default
    max_retry_attempts: int = 5
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, program_period: str = DEFAULT_PROGRAM_PERIOD) -> "AnaptyxiConnectorConfig":
        if program_period not in SUPPORTED_PROGRAM_PERIODS:
            raise ValueError(
                f"unknown program_period {program_period!r}; expected one of {SUPPORTED_PROGRAM_PERIODS}"
            )

        env_var = _ENV_VAR_BY_PERIOD[program_period]
        base_url = (os.environ.get(env_var) or "").strip() or None
        if base_url is None and program_period == DEFAULT_PROGRAM_PERIOD:
            # backward-compatible alias — see module docstring
            base_url = (os.environ.get("ANAPTYXI_API_BASE_URL") or "").strip() or None
        if base_url is None:
            base_url = PUBLIC_API_BASE_URL_BY_PERIOD.get(program_period)

        if not base_url:
            raise AnaptyxiUpstreamContractError(
                f"{program_period} has no validated public project-level API. "
                f"Set {env_var} only after validating the GetData.ashx project-detail "
                "contract; the official 2021-2027 portal currently exposes aggregate "
                "charts, which are not sufficient for procurement-project linkage."
            )
        return cls(
            base_url=base_url,
            program_period=program_period,
            rate_limit_per_minute=_float_env("ANAPTYXI_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
        )
