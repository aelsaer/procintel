"""OpenSearch connection configuration — description.txt §11/§29
("OpenSearch για full-text και document search")."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenSearchConfig:
    base_url: str
    index_name: str = "procurement_acts"
    username: str | None = None
    password: str | None = None
    request_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "OpenSearchConfig":
        base_url = os.environ.get("OPENSEARCH_URL")
        if not base_url:
            raise RuntimeError(
                "OPENSEARCH_URL is not set. See infra/docker/docker-compose.yml's "
                "opensearch service and infra/docker/.env.example."
            )
        return cls(
            base_url=base_url.rstrip("/"),
            index_name=os.environ.get("OPENSEARCH_INDEX_NAME", "procurement_acts"),
            username=os.environ.get("OPENSEARCH_USERNAME"),
            password=os.environ.get("OPENSEARCH_PASSWORD"),
        )
