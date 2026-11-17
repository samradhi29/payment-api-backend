
import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from app.adapters.base import GatewayTimeoutError
from app.adapters.simulated import SimulatedGatewayAdapter
from app.main import create_app
from app.scoring import GatewayMetrics, ScoringEngine


def make_payment():
    return {
        "payment_id": str(uuid4()),
        "amount_minor": 10000,
        "currency": "INR",
        "idempotency_key": "resilience-test-001",
        "payment_method": "UPI",
        "bank_code": "HDFC",
    }


class FailingPublisher:
    async def publish(self, **kwargs):
        raise RuntimeError("Kafka unavailable")

    async def stop(self):
        pass


class FailingSuccessBreaker:
    async def is_open(self, provider):
        return False

    async def record_success(self, provider):
        raise RuntimeError("Redis unavailable")

    async def record_failure(self, provider):
        raise RuntimeError("Redis unavailable")


class FailingCircuitCheckBreaker:
    async def is_open(self, provider):
        raise RuntimeError("Redis unavailable")

    async def record_success(self, provider):
        pass

    async def record_failure(self, provider):
        pass


class NoopBreaker:
    async def is_open(self, provider):
        return False

    async def record_success(self, provider):
        pass

    async def record_failure(self, provider):
        pass


class TrackingGatewayAdapter(SimulatedGatewayAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.call_count = 0

    async def create_order(self, payment):
        self.call_count += 1
        return await super().create_order(payment)


class AsyncTimeoutAdapter(TrackingGatewayAdapter):
    async def create_order(self, payment):
        self.call_count += 1
        raise asyncio.TimeoutError()


def make_registry():
    return {
        "gateway_a": SimulatedGatewayAdapter(
            provider_name="gateway_a",
            mode="success",
        )
    }


def make_engine():
    return ScoringEngine({
        "gateway_a": GatewayMetrics(success_rate=0.99),
    })


def make_two_gateway_registry(first_mode, second_mode="success"):
    return {
        "gateway_a": TrackingGatewayAdapter(
            provider_name="gateway_a",
            mode=first_mode,
        ),
        "gateway_b": TrackingGatewayAdapter(
            provider_name="gateway_b",
            mode=second_mode,
        ),
    }


def make_two_gateway_engine():
    return ScoringEngine({
        "gateway_a": GatewayMetrics(success_rate=0.99),
        "gateway_b": GatewayMetrics(success_rate=0.90),
    })


def test_kafka_failure_does_not_fail_successful_attempt():
    app = create_app(
        registry=make_registry(),
        event_publisher=FailingPublisher(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/attempt?gateway=gateway_a",
            json=make_payment(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "created"


def test_redis_success_record_failure_does_not_fail_payment():
    app = create_app(
        registry=make_registry(),
        scoring_engine=make_engine(),
        circuit_breaker=FailingSuccessBreaker(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/route",
            json=make_payment(),
        )

    assert response.status_code == 200
    assert response.json()["selected_provider"] == "gateway_a"
    assert response.json()["attempted_gateways"] == ["gateway_a"]


def test_redis_circuit_check_failure_skips_gateway():
    app = create_app(
        registry=make_registry(),
        scoring_engine=make_engine(),
        circuit_breaker=FailingCircuitCheckBreaker(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/route",
            json=make_payment(),
        )

    assert response.status_code == 503
    assert "open circuits" in response.json()["detail"].lower()


def test_gateway_timeout_does_not_trigger_failover():
    registry = make_two_gateway_registry(
        first_mode="timeout",
    )

    app = create_app(
        registry=registry,
        scoring_engine=make_two_gateway_engine(),
        circuit_breaker=NoopBreaker(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/route",
            json=make_payment(),
        )

    assert response.status_code == 504
    assert "outcome is unknown" in response.json()["detail"].lower()
    assert "reconcile" in response.json()["detail"].lower()

    assert registry["gateway_a"].call_count == 1
    assert registry["gateway_b"].call_count == 0


def test_asyncio_timeout_does_not_trigger_failover():
    registry = {
        "gateway_a": AsyncTimeoutAdapter(
            provider_name="gateway_a",
            mode="success",
        ),
        "gateway_b": TrackingGatewayAdapter(
            provider_name="gateway_b",
            mode="success",
        ),
    }

    app = create_app(
        registry=registry,
        scoring_engine=make_two_gateway_engine(),
        circuit_breaker=NoopBreaker(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/route",
            json=make_payment(),
        )

    assert response.status_code == 504
    assert "outcome is unknown" in response.json()["detail"].lower()

    assert registry["gateway_a"].call_count == 1
    assert registry["gateway_b"].call_count == 0


def test_confirmed_failure_still_triggers_failover():
    registry = make_two_gateway_registry(
        first_mode="fail",
        second_mode="success",
    )

    app = create_app(
        registry=registry,
        scoring_engine=make_two_gateway_engine(),
        circuit_breaker=NoopBreaker(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/route",
            json=make_payment(),
        )

    assert response.status_code == 200
    assert response.json()["selected_provider"] == "gateway_b"
    assert response.json()["attempted_gateways"] == [
        "gateway_a",
        "gateway_b",
    ]
    assert response.json()["retries_used"] == 1

    assert registry["gateway_a"].call_count == 1
    assert registry["gateway_b"].call_count == 1
