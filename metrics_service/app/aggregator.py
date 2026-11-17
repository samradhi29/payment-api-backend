import math
import time
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from threading import RLock
from typing import Any


WINDOWS_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "60m": 3600,
}

MAX_FUTURE_EVENT_SECONDS = 300


def percentile(values: list[float], percent: float) -> float:
    """Return a linearly interpolated percentile."""
    if not 0.0 <= percent <= 1.0:
        raise ValueError("Percentile must be between 0 and 1")

    if not values:
        return 0.0

    ordered = sorted(values)
    index = (len(ordered) - 1) * percent
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower

    return round(
        ordered[lower] * (1.0 - fraction)
        + ordered[upper] * fraction,
        2,
    )


class MetricsAggregator:
    """Thread-safe in-memory metrics aggregator using event time."""

    def __init__(self, max_events_per_gateway: int = 100_000):
        if (
            isinstance(max_events_per_gateway, bool)
            or not isinstance(max_events_per_gateway, int)
            or max_events_per_gateway < 1
        ):
            raise ValueError(
                "max_events_per_gateway must be a positive integer"
            )

        self.events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.lock = RLock()
        self.max_events_per_gateway = max_events_per_gateway

    @staticmethod
    def _normalize_gateway(gateway: Any) -> str:
        normalized = str(gateway or "").strip().lower()
        if not normalized:
            raise ValueError("Gateway cannot be empty")
        if len(normalized) > 100:
            raise ValueError("Gateway name is too long")
        return normalized

    @staticmethod
    def _event_timestamp(event: dict[str, Any], ingestion_time: float) -> float:
        occurred_at = event.get("occurred_at")
        if occurred_at in (None, ""):
            return ingestion_time

        if not isinstance(occurred_at, str):
            raise ValueError("occurred_at must be a string")

        try:
            parsed = datetime.fromisoformat(
                occurred_at.strip().replace("Z", "+00:00")
            )
        except ValueError:
            raise ValueError("Invalid occurred_at timestamp") from None

        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(
                "occurred_at must include timezone information"
            )

        timestamp = parsed.timestamp()
        if not math.isfinite(timestamp):
            raise ValueError("Invalid event timestamp")

        if timestamp > ingestion_time + MAX_FUTURE_EVENT_SECONDS:
            raise ValueError(
                "Event timestamp is more than five minutes in the future"
            )

        return timestamp

    def record(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            raise ValueError("Event must be a dictionary")

        required = {"gateway", "outcome", "latency_ms"}
        if not required.issubset(event):
            raise ValueError("Event is missing required fields")

        gateway = self._normalize_gateway(event["gateway"])
        outcome = str(event["outcome"]).strip().lower()
        if outcome not in {"success", "failure", "timeout"}:
            raise ValueError("Invalid outcome")

        raw_latency = event["latency_ms"]
        if isinstance(raw_latency, bool):
            raise ValueError("Invalid latency value")
        try:
            latency = float(raw_latency)
        except (TypeError, ValueError):
            raise ValueError("Invalid latency value") from None

        if not math.isfinite(latency) or latency < 0:
            raise ValueError("Latency must be finite and non-negative")

        ingestion_time = time.time()
        timestamp = self._event_timestamp(event, ingestion_time)
        normalized = {
            "timestamp": timestamp,
            "gateway": gateway,
            "payment_method": str(
                event.get("payment_method") or "UNKNOWN"
            ).strip().upper(),
            "bank_code": str(
                event.get("bank_code") or "UNKNOWN"
            ).strip().upper(),
            "outcome": outcome,
            "latency_ms": latency,
        }

        with self.lock:
            gateway_events = self.events[gateway]
            retention_cutoff = ingestion_time - WINDOWS_SECONDS["60m"]

            # Prune by event time, not arrival order. Kafka can deliver delayed
            # events, and a delayed event must not make old samples look new.
            gateway_events[:] = [
                item
                for item in gateway_events
                if item["timestamp"] >= retention_cutoff
            ]

            timestamps = [item["timestamp"] for item in gateway_events]
            insert_at = bisect_right(timestamps, timestamp)
            gateway_events.insert(insert_at, normalized)

            overflow = len(gateway_events) - self.max_events_per_gateway
            if overflow > 0:
                del gateway_events[:overflow]

    def _get_window_events(
        self,
        gateway: str,
        window_seconds: int,
    ) -> list[dict[str, Any]]:
        gateway = self._normalize_gateway(gateway)
        current_time = time.time()
        cutoff = current_time - window_seconds
        retention_cutoff = current_time - WINDOWS_SECONDS["60m"]

        with self.lock:
            gateway_events = self.events.get(gateway, [])
            gateway_events[:] = [
                event
                for event in gateway_events
                if retention_cutoff <= event["timestamp"] <= current_time
            ]
            return [
                event
                for event in gateway_events
                if event["timestamp"] >= cutoff
            ]

    def get_metrics(self, gateway: str, window: str) -> dict[str, Any]:
        if window not in WINDOWS_SECONDS:
            raise ValueError("Unsupported metrics window")

        gateway = self._normalize_gateway(gateway)
        events = self._get_window_events(
            gateway,
            WINDOWS_SECONDS[window],
        )

        latencies = [event["latency_ms"] for event in events]
        success_count = sum(
            event["outcome"] == "success" for event in events
        )
        failure_count = sum(
            event["outcome"] == "failure" for event in events
        )
        timeout_count = sum(
            event["outcome"] == "timeout" for event in events
        )
        total = len(events)

        return {
            "gateway": gateway,
            "window": window,
            "total_attempts": total,
            "success_count": success_count,
            "failure_count": failure_count,
            "timeout_count": timeout_count,
            "success_rate": (
                round(success_count / total, 4) if total else 0.0
            ),
            "p50_latency_ms": percentile(latencies, 0.50),
            "p95_latency_ms": percentile(latencies, 0.95),
            "p99_latency_ms": percentile(latencies, 0.99),
        }

    def get_all_metrics(self) -> dict[str, Any]:
        with self.lock:
            gateways = sorted(self.events.keys())

        return {
            gateway: {
                window: self.get_metrics(gateway, window)
                for window in WINDOWS_SECONDS
            }
            for gateway in gateways
        }
