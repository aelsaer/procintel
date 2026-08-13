from scripts.publish_operational_metrics import MISSING_SOURCE_AGE_SECONDS, build_metric_data


def test_metric_payload_marks_missing_required_sources_as_stale():
    metrics = build_metric_data(
        {"KHMDHS": 120.0},
        {"EnrichmentQueueDepth": 17},
        required_sources=("KHMDHS", "GEMI"),
    )

    by_source = {
        metric["Dimensions"][0]["Value"]: metric["Value"]
        for metric in metrics
        if metric["MetricName"] == "SourceFreshnessSeconds"
    }
    assert by_source == {"KHMDHS": 120.0, "GEMI": float(MISSING_SOURCE_AGE_SECONDS)}
    assert metrics[-1] == {"MetricName": "EnrichmentQueueDepth", "Unit": "Count", "Value": 17.0}


def test_metric_payload_preserves_each_durable_queue_metric():
    metrics = build_metric_data(
        {},
        {
            "FetchQueueDepth": 2,
            "ExportQueueDepth": 3,
            "ScoringQueueDepth": 4,
            "WebhookQueueDepth": 5,
            "DigestQueueDepth": 6,
            "ReminderQueueDepth": 7,
            "OldestDurableJobAgeSeconds": 600,
        },
        required_sources=(),
    )

    assert {metric["MetricName"]: metric["Value"] for metric in metrics} == {
        "FetchQueueDepth": 2.0,
        "ExportQueueDepth": 3.0,
        "ScoringQueueDepth": 4.0,
        "WebhookQueueDepth": 5.0,
        "DigestQueueDepth": 6.0,
        "ReminderQueueDepth": 7.0,
        "OldestDurableJobAgeSeconds": 600.0,
    }
