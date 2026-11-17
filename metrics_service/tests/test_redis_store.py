import json

import pytest

from app.redis_store import RedisMetricsStore


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def zadd(self, key, mapping):
        entries = self.data.setdefault(key, {})
        for member, score in mapping.items():
            entries[member] = score

    async def zremrangebyscore(self, key, minimum, maximum):
        entries = self.data.setdefault(key, {})
        maximum = float(maximum)

        removed = [
            member for member, score in entries.items()
            if score <= maximum
        ]

        for member in removed:
            del entries[member]

        return len(removed)

    async def zrangebyscore(self, key, minimum, maximum):
        entries = self.data.get(key, {})
        minimum = float(minimum)
        maximum = float("inf") if maximum == "+inf" else float(maximum)

        return [
            member
            for member, score in sorted(
                entries.items(), key=lambda item: item[1]
            )
            if minimum <= score <= maximum
        ]

    async def eval(self, script, numkeys, *keys_and_args):
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        event_key, deduplication_key = keys[0], keys[1]
        timestamp, member, retention_cutoff, retention_seconds = args[0], args[1], args[2], args[3]

        if deduplication_key in self.data:
            return 0
        self.data[deduplication_key] = "1"
        await self.zadd(event_key, {member: float(timestamp)})
        await self.zremrangebyscore(event_key, "-inf", float(retention_cutoff))
        return 1


def make_event(event_id, outcome="success", latency=100):
    from datetime import datetime, timezone

    return {
        "event_id": event_id,
        "gateway": "mock_alpha",
        "outcome": outcome,
        "latency_ms": latency,
        "payment_method": "CARD",
        "bank_code": "HDFC",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.asyncio
async def test_records_and_reads_metrics():
    store = RedisMetricsStore(FakeRedis())

    await store.record(make_event("event-1", "success", 100))
    await store.record(make_event("event-2", "failure", 200))

    result = await store.get_metrics("mock_alpha", "1m")

    assert result["total_attempts"] == 2
    assert result["success_count"] == 1
    assert result["failure_count"] == 1


@pytest.mark.asyncio
async def test_duplicate_event_is_not_counted_twice():
    store = RedisMetricsStore(FakeRedis())
    event = make_event("event-1")

    await store.record(event)
    await store.record(event)

    result = await store.get_metrics("mock_alpha", "1m")

    assert result["total_attempts"] == 1


@pytest.mark.asyncio
async def test_calculates_percentiles():
    store = RedisMetricsStore(FakeRedis())

    for number in range(1, 101):
        await store.record(
            make_event(f"event-{number}", latency=number)
        )

    result = await store.get_metrics("mock_alpha", "1m")

    assert result["p50_latency_ms"] == 50.5
    assert result["p95_latency_ms"] == 95.05
    assert result["p99_latency_ms"] == 99.01


@pytest.mark.asyncio
async def test_rejects_invalid_outcome():
    store = RedisMetricsStore(FakeRedis())

    with pytest.raises(ValueError):
        await store.record(make_event("event-1", "pending"))


@pytest.mark.asyncio
async def test_rejects_invalid_window():
    store = RedisMetricsStore(FakeRedis())

    with pytest.raises(ValueError):
        await store.get_metrics("mock_alpha", "24h")
