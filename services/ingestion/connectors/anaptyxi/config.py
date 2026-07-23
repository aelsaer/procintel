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

`base_url` has no default for any period — same discipline as every other
connector.
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
        base_url = os.environ.get(env_var)
        if base_url is None and program_period == DEFAULT_PROGRAM_PERIOD:
            # backward-compatible alias — see module docstring
            base_url = os.environ.get("ANAPTYXI_API_BASE_URL")

        if not base_url:
            raise RuntimeError(
                f"{env_var} is not set. See docs/source-contracts/anaptyxi.md — confirm the "
                f"base hostname and endpoint paths for {program_period} against its own live "
                "Open Data documentation before setting this (each programming period is a "
                "separate deployment, not a query parameter on one system)."
            )
        return cls(
            base_url=base_url,
            program_period=program_period,
            rate_limit_per_minute=_float_env("ANAPTYXI_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
        )
