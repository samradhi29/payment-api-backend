import json

import pytest

from app.consumer import KafkaMetricsConsumer


class FakeMetricsStore:
    def __init__(self):
        self.events = []

    async def record(self, event):
        self.events.append(event)


class FakeMessage:
    def __init__(self, value, offset=1):
        self.value = value
        self.offset = offset


@pytest.mark.asyncio
async def test_processes_valid_kafka_message():
    store = FakeMetricsStore()
    consumer = KafkaMetricsConsumer(store)

    event = {
        "gateway": "mock_alpha",
        "outcome": "success",
        "latency_ms": 120,
    }

    message = FakeMessage(json.dumps(event).encode("utf-8"))

    await consumer.process_message(message)

    assert len(store.events) == 1
    assert store.events[0]["gateway"] == "mock_alpha"


@pytest.mark.asyncio
async def test_rejects_invalid_json():
    store = FakeMetricsStore()
    consumer = KafkaMetricsConsumer(store)

    message = FakeMessage(b"not-valid-json")

    with pytest.raises(json.JSONDecodeError):
        await consumer.process_message(message)

    assert store.events == []


@pytest.mark.asyncio
async def test_rejects_non_object_json():
    store = FakeMetricsStore()
    consumer = KafkaMetricsConsumer(store)

    message = FakeMessage(json.dumps(["invalid", "event"]).encode())

    with pytest.raises(ValueError):
        await consumer.process_message(message)

    assert store.events == []


@pytest.mark.asyncio
async def test_accepts_raw_bytes():
    store = FakeMetricsStore()
    consumer = KafkaMetricsConsumer(store)

    event = {
        "gateway": "mock_beta",
        "outcome": "failure",
        "latency_ms": 250,
    }

    await consumer.process_message(
        json.dumps(event).encode("utf-8")
    )

    assert len(store.events) == 1
