
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.adapters.simulated import SimulatedGatewayAdapter
from app.main import create_app


PAYMENT = {
    "payment_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "amount_minor": 10000,
    "currency": "INR",
    "idempotency_key": "test-idempotency-key-001",
}


class FakeScoringEngine:
    def __init__(self):
        self.refreshed_providers = []

    async def refresh_from_redis(self, redis_client, providers):
        self.refreshed_providers = list(providers)

    def rank(self, registry, payment):
        candidates = []

        for provider in registry:
            candidates.append(
                SimpleNamespace(
                    provider=provider,
                    score=0.95 if provider == "cashfree" else 0.85,
                    breakdown={
                        "success_rate": 0.35,
                        "latency": 0.25,
                    },
                    available=True,
                )
            )

        return sorted(
            candidates,
            key=lambda candidate: candidate.score,
            reverse=True,
        )


class FakeCircuitBreaker:
    def __init__(self, open_providers=None):
        self.open_providers = set(open_providers or [])

    async def is_open(self, provider):
        return provider in self.open_providers

    async def record_success(self, provider):
        return None

    async def record_failure(self, provider):
        return None


class CountingSimulatedAdapter(SimulatedGatewayAdapter):
    def __init__(self, provider_name, mode="success"):
        super().__init__(provider_name, mode=mode)
        self.create_order_calls = 0

    async def create_order(self, payment):
        self.create_order_calls += 1
        return await super().create_order(payment)


def make_client(mode="success"):
    registry = {
        "mock_alpha": SimulatedGatewayAdapter(
            "mock_alpha",
            mode=mode,
        )
    }

    return TestClient(create_app(registry=registry))


def make_decision_client(registry=None, open_providers=None):
    if registry is None:
        registry = {
            "mock_alpha": SimulatedGatewayAdapter("mock_alpha")
        }

    return TestClient(
        create_app(
            registry=registry,
            scoring_engine=FakeScoringEngine(),
            circuit_breaker=FakeCircuitBreaker(open_providers),
        )
    )


def test_simulated_gateway_success():
    client = make_client()

    response = client.post(
        "/v1/router/attempt?gateway=mock_alpha",
        json=PAYMENT,
    )

    assert response.status_code == 200

    body = response.json()
    assert body["provider"] == "mock_alpha"
    assert body["status"] == "created"
    assert body["simulated"] is True
    assert body["amount_minor"] == 10000


def test_simulated_gateway_failure():
    client = make_client(mode="fail")

    response = client.post(
        "/v1/router/attempt?gateway=mock_alpha",
        json=PAYMENT,
    )

    assert response.status_code == 502


def test_simulated_gateway_timeout():
    client = make_client(mode="timeout")

    response = client.post(
        "/v1/router/attempt?gateway=mock_alpha",
        json=PAYMENT,
    )

    assert response.status_code == 504


def test_unknown_gateway():
    client = make_client()

    response = client.post(
        "/v1/router/attempt?gateway=unknown",
        json=PAYMENT,
    )

    assert response.status_code == 404


def test_invalid_amount():
    client = make_client()

    invalid_payment = {
        **PAYMENT,
        "amount_minor": 0,
    }

    response = client.post(
        "/v1/router/attempt?gateway=mock_alpha",
        json=invalid_payment,
    )

    assert response.status_code == 422


def test_decision_includes_allowed_providers_without_execution_adapters():
    client = make_decision_client(
        registry={
            "mock_alpha": SimulatedGatewayAdapter("mock_alpha")
        }
    )

    response = client.post(
        "/v1/router/decision"
        "?allowed_providers=cashfree"
        "&allowed_providers=payu",
        json=PAYMENT,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["payment_id"] == PAYMENT["payment_id"]
    assert body["selected_provider"] == "cashfree"
    assert [item["provider"] for item in body["candidates"]] == [
        "cashfree",
        "payu",
    ]


def test_decision_excludes_open_circuit():
    client = make_decision_client(
        open_providers={"cashfree"},
    )

    response = client.post(
        "/v1/router/decision"
        "?allowed_providers=cashfree"
        "&allowed_providers=payu",
        json=PAYMENT,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["selected_provider"] == "payu"
    assert [item["provider"] for item in body["candidates"]] == [
        "payu",
    ]


def test_decision_does_not_create_gateway_order():
    adapter = CountingSimulatedAdapter("mock_alpha")

    client = make_decision_client(
        registry={"mock_alpha": adapter},
    )

    response = client.post(
        "/v1/router/decision?allowed_providers=mock_alpha",
        json=PAYMENT,
    )

    assert response.status_code == 200
    assert adapter.create_order_calls == 0


def test_decision_returns_503_when_all_allowed_circuits_are_open():
    client = make_decision_client(
        open_providers={"cashfree", "payu"},
    )

    response = client.post(
        "/v1/router/decision"
        "?allowed_providers=cashfree"
        "&allowed_providers=payu",
        json=PAYMENT,
    )

    assert response.status_code == 503


def test_decision_rejects_empty_allowed_providers():
    client = make_decision_client()

    response = client.post(
        "/v1/router/decision",
        json=PAYMENT,
    )

    assert response.status_code == 422
