
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.scoring import GatewayMetrics, ScoringEngine


def make_payment(
    amount_minor=10000,
    bank_code="HDFC",
    payment_method="UPI",
):
    return SimpleNamespace(
        payment_id=uuid4(),
        amount_minor=amount_minor,
        bank_code=bank_code,
        payment_method=payment_method,
    )


class FakeRedis:
    def __init__(self, events=None, error=None):
        self.events = events or []
        self.error = error

    async def zrangebyscore(self, key, minimum, maximum):
        if self.error:
            raise self.error

        return self.events


def make_event(
    outcome="failure",
    latency_ms=10.0,
    bank_code="HDFC",
    payment_method="UPI",
):
    return json.dumps({
        "outcome": outcome,
        "latency_ms": latency_ms,
        "bank_code": bank_code,
        "payment_method": payment_method,
    })


def test_higher_success_rate_gets_higher_score():
    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(success_rate=0.99),
            "gateway_b": GatewayMetrics(success_rate=0.70),
        }
    )

    payment = make_payment()

    score_a = engine.score("gateway_a", payment)
    score_b = engine.score("gateway_b", payment)

    assert score_a.score > score_b.score


def test_lower_latency_gets_higher_score():
    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(p95_latency_ms=100),
            "gateway_b": GatewayMetrics(p95_latency_ms=1500),
        }
    )

    payment = make_payment()

    assert (
        engine.score("gateway_a", payment).score
        > engine.score("gateway_b", payment).score
    )


def test_bank_affinity_influences_score():
    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(
                bank_affinity={"HDFC": 1.0}
            ),
            "gateway_b": GatewayMetrics(
                bank_affinity={"HDFC": 0.1}
            ),
        }
    )

    payment = make_payment()

    assert (
        engine.score("gateway_a", payment).score
        > engine.score("gateway_b", payment).score
    )


def test_method_affinity_influences_score():
    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(
                method_affinity={"UPI": 1.0}
            ),
            "gateway_b": GatewayMetrics(
                method_affinity={"UPI": 0.0}
            ),
        }
    )

    payment = make_payment()

    assert (
        engine.score("gateway_a", payment).score
        > engine.score("gateway_b", payment).score
    )


def test_amount_outside_gateway_range_gets_zero_fit():
    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(
                min_amount_minor=1000,
                max_amount_minor=5000,
            ),
            "gateway_b": GatewayMetrics(),
        }
    )

    payment = make_payment(amount_minor=10000)

    score_a = engine.score("gateway_a", payment)
    score_b = engine.score("gateway_b", payment)

    assert score_a.breakdown["amount_fit"] == 0.0
    assert score_b.breakdown["amount_fit"] == 0.5


def test_open_circuit_gateway_is_unavailable():
    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(circuit_open=True),
            "gateway_b": GatewayMetrics(circuit_open=False),
        }
    )

    payment = make_payment()

    score_a = engine.score("gateway_a", payment)
    score_b = engine.score("gateway_b", payment)

    assert score_a.available is False
    assert score_a.score == 0.0
    assert score_b.available is True


def test_rank_places_best_gateway_first():
    engine = ScoringEngine(
        {
            "gateway_a": GatewayMetrics(success_rate=0.70),
            "gateway_b": GatewayMetrics(success_rate=0.99),
            "gateway_c": GatewayMetrics(circuit_open=True),
        }
    )

    ranked = engine.rank(
        {
            "gateway_a": object(),
            "gateway_b": object(),
            "gateway_c": object(),
        },
        make_payment(),
    )

    assert ranked[0].provider == "gateway_b"
    assert ranked[-1].provider == "gateway_c"


def test_single_failure_is_smoothed_toward_baseline():
    engine = ScoringEngine()

    redis = FakeRedis([
        make_event(
            outcome="failure",
            latency_ms=10.0,
        )
    ])

    asyncio.run(
        engine.refresh_from_redis(redis, ["gateway_a"])
    )

    metrics = engine.metrics["gateway_a"]

    assert metrics.success_rate == pytest.approx(0.7916667)
    assert metrics.bank_affinity["HDFC"] == pytest.approx(0.4166667)
    assert metrics.method_affinity["UPI"] == pytest.approx(0.4166667)


def test_single_latency_observation_is_smoothed():
    engine = ScoringEngine()

    redis = FakeRedis([
        make_event(
            outcome="failure",
            latency_ms=10.0,
        )
    ])

    asyncio.run(
        engine.refresh_from_redis(redis, ["gateway_a"])
    )

    assert engine.metrics["gateway_a"].p95_latency_ms == pytest.approx(
        835.0
    )


def test_large_sample_has_stronger_influence_than_small_sample():
    engine = ScoringEngine()

    events = [
        make_event(outcome="failure")
        for _ in range(100)
    ]

    redis = FakeRedis(events)

    asyncio.run(
        engine.refresh_from_redis(redis, ["gateway_a"])
    )

    assert engine.metrics["gateway_a"].success_rate < 0.1


def test_empty_recent_window_resets_metrics_to_defaults():
    engine = ScoringEngine({
        "gateway_a": GatewayMetrics(
            success_rate=0.2,
            p95_latency_ms=1800,
            bank_affinity={"HDFC": 0.1},
            method_affinity={"UPI": 0.1},
            min_amount_minor=1000,
            max_amount_minor=50000,
            circuit_open=True,
        )
    })

    asyncio.run(
        engine.refresh_from_redis(
            FakeRedis(),
            ["gateway_a"],
        )
    )

    metrics = engine.metrics["gateway_a"]

    assert metrics.success_rate == 0.95
    assert metrics.p95_latency_ms == 1000.0
    assert metrics.bank_affinity == {}
    assert metrics.method_affinity == {}
    assert metrics.min_amount_minor == 1000
    assert metrics.max_amount_minor == 50000
    assert metrics.circuit_open is True


def test_redis_error_preserves_last_known_good_metrics():
    existing = GatewayMetrics(
        success_rate=0.82,
        p95_latency_ms=450,
        bank_affinity={"HDFC": 0.9},
        method_affinity={"UPI": 0.8},
    )

    engine = ScoringEngine({
        "gateway_a": existing,
    })

    redis = FakeRedis(error=ConnectionError("Redis unavailable"))

    asyncio.run(
        engine.refresh_from_redis(
            redis,
            ["gateway_a"],
        )
    )

    assert engine.metrics["gateway_a"] is existing
    assert engine.metrics["gateway_a"].success_rate == 0.82
    assert engine.metrics["gateway_a"].p95_latency_ms == 450


def test_missing_redis_client_preserves_existing_metrics():
    existing = GatewayMetrics(success_rate=0.75)

    engine = ScoringEngine({
        "gateway_a": existing,
    })

    asyncio.run(
        engine.refresh_from_redis(
            None,
            ["gateway_a"],
        )
    )

    assert engine.metrics["gateway_a"] is existing
    assert engine.metrics["gateway_a"].success_rate == 0.75
