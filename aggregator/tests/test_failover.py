
import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from app.adapters.simulated import SimulatedGatewayAdapter
from app.circuit_breaker import RedisCircuitBreaker
from app.main import create_app
from app.models import RouterPaymentRequest
from app.scoring import GatewayMetrics, ScoringEngine


import json

class FakeRedis:
    def __init__(self):
        self.values = {}

    async def exists(self, *keys):
        return sum(1 for k in keys if k in self.values)

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key, seconds):
        return True

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
        return deleted

    async def ping(self):
        return True

    async def aclose(self):
        pass



    async def eval(self, script, numkeys, *keys_and_args):
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])

        if 'failures' in script and 'EXPIRE' in script:
            failure_key, open_key, half_open_key, probe_key = keys[0], keys[1], keys[2], keys[3]
            failures = int(self.values.get(failure_key, 0)) + 1
            threshold = int(args[1]) if len(args) > 1 else 3
            if half_open_key in self.values:
                self.values[open_key] = '1'
                self.values.pop(failure_key, None)
                self.values.pop(probe_key, None)
                return threshold
            if failures >= threshold:
                self.values[open_key] = '1'
                self.values[half_open_key] = '1'
                self.values.pop(failure_key, None)
                self.values.pop(probe_key, None)
            else:
                self.values[failure_key] = failures
            return failures
        elif 'DEL' in script and len(keys) >= 4:
            for k in keys:
                self.values.pop(k, None)
            return 1
        elif 'NX' in script:
            open_key, half_open_key, probe_key = keys[0], keys[1], keys[2]
            if open_key in self.values:
                return 0
            if half_open_key not in self.values:
                return 2
            if probe_key not in self.values:
                self.values[probe_key] = 'probe'
                return 1
            return 0
        elif len(keys) == 3 and 'EXISTS' in script and 'return 0' in script:
            open_key, half_open_key, probe_key = keys[0], keys[1], keys[2]
            if open_key in self.values:
                return 1
            if half_open_key in self.values and probe_key in self.values:
                return 1
            return 0
        elif 'payment_fingerprint' in script:
            key = keys[0]
            raw = self.values.get(key)
            if not raw:
                self.values[key] = args[0]
                return 1
            existing = json.loads(raw)
            if existing.get('payment_fingerprint') != args[1]:
                return -1
            if existing.get('status') == 'completed':
                return 0
            if existing.get('status') == 'retryable':
                replacement = json.loads(args[0])
                replacement['attempted_providers'] = existing.get('attempted_providers', [])
                replacement['previous_attempts'] = existing.get('previous_attempts', existing.get('attempted_providers', []))
                replacement['created_at'] = existing.get('created_at')
                self.values[key] = json.dumps(replacement)
                return 1
            return 0
        elif 'attempted_providers' in script:
            key = keys[0]
            raw = self.values.get(key)
            if not raw:
                return -1
            st = json.loads(raw)
            if st.get('status') != 'in_progress':
                return -2
            st['current_provider'] = args[0]
            att = st.setdefault('attempted_providers', [])
            if args[0] not in att:
                att.append(args[0])
            self.values[key] = json.dumps(st)
            return json.dumps(st)
        elif 'state.status = \'retryable\'' in script or 'status = \'retryable\'' in script:
            key = keys[0]
            raw = self.values.get(key)
            if not raw:
                return 0
            st = json.loads(raw)
            if st.get('status') != 'in_progress':
                return 0
            if len(args) > 1 and args[1] and st.get('current_provider') != args[1]:
                return 0
            st['status'] = 'retryable'
            st['current_provider'] = None
            st['last_error'] = args[0]
            self.values[key] = json.dumps(st)
            return 1
        elif 'state.status = \'completed\'' in script or 'status = \'completed\'' in script:
            key = keys[0]
            raw = self.values.get(key)
            if not raw:
                return -1
            st = json.loads(raw)
            st['status'] = 'completed'
            st['result'] = json.loads(args[0]) if isinstance(args[0], str) else args[0]
            self.values[key] = json.dumps(st)
            return 1
        elif 'last_error = ARGV[1]' in script:
            key = keys[0]
            raw = self.values.get(key)
            if not raw:
                return 0
            st = json.loads(raw)
            if st.get('status') == 'in_progress':
                st['last_error'] = args[0]
                self.values[key] = json.dumps(st)
                return 1
            return 0
        return 1


class RecordingPublisher:
    def __init__(self):
        self.events = []

    async def publish(self, **event):
        self.events.append(event)

    async def stop(self):
        pass


def make_payment():
    return RouterPaymentRequest(
        payment_id=uuid4(),
        amount_minor=10000,
        currency="INR",
        idempotency_key="failover-test-001",
        payment_method="UPI",
        bank_code="HDFC",
    )


def test_circuit_opens_after_three_failures():
    redis = FakeRedis()
    breaker = RedisCircuitBreaker(redis)

    async def run_test():
        assert await breaker.is_open("gateway_a") is False

        await breaker.record_failure("gateway_a")
        await breaker.record_failure("gateway_a")

        assert await breaker.is_open("gateway_a") is False

        await breaker.record_failure("gateway_a")

        assert await breaker.is_open("gateway_a") is True

    asyncio.run(run_test())


def test_success_resets_circuit_state():
    redis = FakeRedis()
    breaker = RedisCircuitBreaker(redis)

    async def run_test():
        await breaker.record_failure("gateway_a")
        await breaker.record_success("gateway_a")

        assert await breaker.is_open("gateway_a") is False

        failure_key = "aggregator:circuit:gateway_a:failures"
        open_key = "aggregator:circuit:gateway_a:open"

        assert failure_key not in redis.values
        assert open_key not in redis.values

    asyncio.run(run_test())


def test_router_fails_over_to_next_gateway():
    redis = FakeRedis()
    breaker = RedisCircuitBreaker(redis)
    publisher = RecordingPublisher()

    # Simulate a previous failure for gateway B.
    asyncio.run(breaker.record_failure("gateway_b"))

    registry = {
        "gateway_a": SimulatedGatewayAdapter(
            provider_name="gateway_a",
            mode="fail",
        ),
        "gateway_b": SimulatedGatewayAdapter(
            provider_name="gateway_b",
            mode="success",
        ),
    }

    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(success_rate=0.99),
            "gateway_b": GatewayMetrics(success_rate=0.50),
        }
    )

    app = create_app(
        registry=registry,
        scoring_engine=engine,
        circuit_breaker=breaker,
        event_publisher=publisher,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/route",
            json=make_payment().model_dump(mode="json"),
        )

    assert response.status_code == 200

    body = response.json()

    # Verify routing and failover.
    assert body["selected_provider"] == "gateway_b"
    assert body["attempted_gateways"] == [
        "gateway_a",
        "gateway_b",
    ]
    assert body["retries_used"] == 1

    # Verify gateway A's failure was recorded.
    gateway_a_failure_key = (
        "aggregator:circuit:gateway_a:failures"
    )

    assert redis.values[gateway_a_failure_key] == 1

    # Verify gateway B's successful attempt cleared its
    # previous failure state.
    gateway_b_failure_key = (
        "aggregator:circuit:gateway_b:failures"
    )

    gateway_b_open_key = (
        "aggregator:circuit:gateway_b:open"
    )

    assert gateway_b_failure_key not in redis.values
    assert gateway_b_open_key not in redis.values

    # Verify telemetry was published for both attempts.
    assert len(publisher.events) == 2

    assert [
        event["outcome"]
        for event in publisher.events
    ] == [
        "failure",
        "success",
    ]

    assert [
        event["gateway"]
        for event in publisher.events
    ] == [
        "gateway_a",
        "gateway_b",
    ]

    for event in publisher.events:
        assert event["payment_id"] == body["result"]["payment_id"]
        assert event["payment_method"] == "UPI"
        assert event["bank_code"] == "HDFC"
        assert event["amount_minor"] == 10000
        assert event["currency"] == "INR"


def test_router_skips_open_circuit():
    redis = FakeRedis()
    breaker = RedisCircuitBreaker(redis)

    async def open_circuit():
        for _ in range(3):
            await breaker.record_failure("gateway_a")

    asyncio.run(open_circuit())

    registry = {
        "gateway_a": SimulatedGatewayAdapter(
            provider_name="gateway_a",
            mode="success",
        ),
        "gateway_b": SimulatedGatewayAdapter(
            provider_name="gateway_b",
            mode="success",
        ),
    }

    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(success_rate=0.99),
            "gateway_b": GatewayMetrics(success_rate=0.50),
        }
    )

    app = create_app(
        registry=registry,
        scoring_engine=engine,
        circuit_breaker=breaker,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/router/route",
            json=make_payment().model_dump(mode="json"),
        )

    assert response.status_code == 200
    assert response.json()["selected_provider"] == "gateway_b"
    assert response.json()["attempted_gateways"] == ["gateway_b"]
