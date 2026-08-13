#!/usr/bin/env python3
"""Publish database-backed data-plane health metrics to CloudWatch."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

REQUIRED_SOURCES = ("KHMDHS", "DIAVGEIA", "GEMI", "TED")
MISSING_SOURCE_AGE_SECONDS = 10 * 365 * 24 * 60 * 60


def _async_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def build_metric_data(
    source_ages: dict[str, float | None],
    queue_counts: dict[str, int],
    *,
    required_sources: tuple[str, ...] = REQUIRED_SOURCES,
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for source in required_sources:
        age = source_ages.get(source)
        metrics.append(
            {
                "MetricName": "SourceFreshnessSeconds",
                "Dimensions": [{"Name": "Source", "Value": source}],
                "Unit": "Seconds",
                "Value": float(age if age is not None else MISSING_SOURCE_AGE_SECONDS),
            }
        )
    for metric_name, value in queue_counts.items():
        metrics.append({"MetricName": metric_name, "Unit": "Count", "Value": float(value)})
    return metrics


async def collect_metrics(database_url: str) -> list[dict[str, Any]]:
    engine = create_async_engine(_async_url(database_url), pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            source_rows = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT source_system,
                               EXTRACT(EPOCH FROM (clock_timestamp() - MAX(fetched_at))) AS age_seconds
                        FROM source_records
                        GROUP BY source_system
                        """
                    )
                )
            ).all()
            counts = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM enrichment_jobs
                           WHERE status IN ('QUEUED', 'FAILED') AND available_at <= now()) AS enrichment_queue,
                          (SELECT COUNT(*) FROM enrichment_jobs WHERE status = 'DEAD') AS enrichment_dead,
                          (SELECT COUNT(*) FROM geospatial_enrichment_jobs
                           WHERE status IN ('QUEUED', 'FAILED') AND available_at <= now()) AS geospatial_queue,
                          durable.fetch_queue,
                          durable.export_queue,
                          durable.scoring_queue,
                          durable.webhook_queue,
                          digest.digest_queue,
                          reminder.reminder_queue,
                          GREATEST(
                            durable.oldest_durable_job_age_seconds,
                            digest.oldest_digest_job_age_seconds,
                            reminder.oldest_reminder_job_age_seconds
                          ) AS oldest_durable_job_age,
                          (SELECT COUNT(*) FROM data_quality_issues
                           WHERE status = 'OPEN' AND severity = 'ERROR') AS quality_errors,
                          EXTRACT(EPOCH FROM (
                            clock_timestamp() - COALESCE(
                              (SELECT MAX(finished_at) FROM connector_runs
                               WHERE status IN ('SUCCEEDED', 'PARTIAL')),
                              TIMESTAMPTZ '1970-01-01'
                            )
                          )) AS ingestion_age
                        FROM procintel_operational_queue_metrics() AS durable
                        CROSS JOIN procintel_digest_queue_metrics() AS digest
                        CROSS JOIN procintel_reminder_queue_metrics() AS reminder
                        """
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    source_ages = {
        str(row.source_system).upper(): float(row.age_seconds) if row.age_seconds is not None else None
        for row in source_rows
    }
    queue_counts = {
        "EnrichmentQueueDepth": int(counts.enrichment_queue),
        "EnrichmentDeadJobs": int(counts.enrichment_dead),
        "GeospatialQueueDepth": int(counts.geospatial_queue),
        "FetchQueueDepth": int(counts.fetch_queue),
        "ExportQueueDepth": int(counts.export_queue),
        "ScoringQueueDepth": int(counts.scoring_queue),
        "WebhookQueueDepth": int(counts.webhook_queue),
        "DigestQueueDepth": int(counts.digest_queue),
        "ReminderQueueDepth": int(counts.reminder_queue),
        "OldestDurableJobAgeSeconds": int(counts.oldest_durable_job_age),
        "OpenQualityErrors": int(counts.quality_errors),
        "LastSuccessfulIngestionAgeSeconds": int(counts.ingestion_age),
    }
    return build_metric_data(source_ages, queue_counts)


def main() -> None:
    import boto3

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    namespace = os.environ.get("PROCINTEL_CLOUDWATCH_NAMESPACE", "Procintel")
    metrics = asyncio.run(collect_metrics(database_url))
    client = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION"))
    for start in range(0, len(metrics), 1000):
        client.put_metric_data(Namespace=namespace, MetricData=metrics[start : start + 1000])
    print(f"published {len(metrics)} operational metrics to {namespace}")


if __name__ == "__main__":
    main()
