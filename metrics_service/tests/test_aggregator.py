import pytest

from app.aggregator import MetricsAggregator, percentile


def make_event(outcome="success", latency=100):
    return {
        "gateway": "mock_alpha",
        "payment_method": "CARD",
        "bank_code": "HDFC",
        "outcome": outcome,
        "latency_ms": latency,
    }


def test_records_successful_attempt():
    metrics = MetricsAggregator()
    metrics.record(make_event("success", 120))

    result = metrics.get_metrics("mock_alpha", "1m")

    assert result["total_attempts"] == 1
    assert result["success_count"] == 1
    assert result["success_rate"] == 1.0


def test_tracks_failure_and_timeout():
    metrics = MetricsAggregator()
    metrics.record(make_event("success", 100))
    metrics.record(make_event("failure", 200))
    metrics.record(make_event("timeout", 300))

    result = metrics.get_metrics("mock_alpha", "5m")

    assert result["total_attempts"] == 3
    assert result["success_count"] == 1
    assert result["failure_count"] == 1
    assert result["timeout_count"] == 1


def test_calculates_latency_percentiles():
    metrics = MetricsAggregator()

    for latency in range(1, 101):
        metrics.record(make_event("success", latency))

    result = metrics.get_metrics("mock_alpha", "1m")

    assert result["p50_latency_ms"] == 50.5
    assert result["p95_latency_ms"] == 95.05
    assert result["p99_latency_ms"] == 99.01


def test_empty_gateway_returns_zero_metrics():
    metrics = MetricsAggregator()

    result = metrics.get_metrics("unknown_gateway", "1m")

    assert result["total_attempts"] == 0
    assert result["p95_latency_ms"] == 0.0


def test_rejects_invalid_outcome():
    metrics = MetricsAggregator()

    with pytest.raises(ValueError):
        metrics.record(make_event("pending", 100))


def test_rejects_negative_latency():
    metrics = MetricsAggregator()

    with pytest.raises(ValueError):
        metrics.record(make_event("success", -10))


def test_rejects_invalid_window():
    metrics = MetricsAggregator()

    with pytest.raises(ValueError):
        metrics.get_metrics("mock_alpha", "24h")


def test_percentile_handles_empty_values():
    assert percentile([], 0.95) == 0.0
