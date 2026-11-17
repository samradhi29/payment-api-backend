from fastapi.testclient import TestClient

from app.main import create_app


class FakeMetricsStore:
    async def get_all_metrics(self):
        return {
            "mock_alpha": {
                "1m": {
                    "gateway": "mock_alpha",
                    "window": "1m",
                    "total_attempts": 2,
                    "success_count": 2,
                    "failure_count": 0,
                    "timeout_count": 0,
                    "success_rate": 1.0,
                    "p50_latency_ms": 100.0,
                    "p95_latency_ms": 120.0,
                    "p99_latency_ms": 120.0,
                }
            }
        }

    async def get_metrics(self, gateway, window):
        if window not in {"1m", "5m", "15m", "60m"}:
            raise ValueError("Unsupported metrics window")

        return {
            "gateway": gateway,
            "window": window,
            "total_attempts": 2,
            "success_count": 2,
            "failure_count": 0,
            "timeout_count": 0,
            "success_rate": 1.0,
            "p50_latency_ms": 100.0,
            "p95_latency_ms": 120.0,
            "p99_latency_ms": 120.0,
        }


def test_health_endpoint():
    app = create_app(
        metrics_store=FakeMetricsStore(),
        snapshot_store=object(),
        start_background=False,
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_get_all_metrics():
    app = create_app(
        metrics_store=FakeMetricsStore(),
        snapshot_store=object(),
        start_background=False,
    )

    with TestClient(app) as client:
        response = client.get("/v1/metrics")

    assert response.status_code == 200
    assert "mock_alpha" in response.json()


def test_get_gateway_metrics():
    app = create_app(
        metrics_store=FakeMetricsStore(),
        snapshot_store=object(),
        start_background=False,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/metrics/mock_alpha?window=5m"
        )

    assert response.status_code == 200
    assert response.json()["window"] == "5m"


def test_rejects_invalid_window():
    app = create_app(
        metrics_store=FakeMetricsStore(),
        snapshot_store=object(),
        start_background=False,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/metrics/mock_alpha?window=24h"
        )

    assert response.status_code == 400
