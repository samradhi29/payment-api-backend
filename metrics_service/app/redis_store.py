
import json
import math
import time
from datetime import datetime, timezone

from app.aggregator import WINDOWS_SECONDS, percentile


class RedisMetricsStore:
    # Atomically deduplicate, insert and remove expired metrics.
    RECORD_EVENT_SCRIPT = """
    if redis.call("EXISTS", KEYS[2]) == 1 then
        return 0
    end

    redis.call("ZADD", KEYS[1], ARGV[1], ARGV[2])

    redis.call(
        "ZREMRANGEBYSCORE",
        KEYS[1],
        "-inf",
        ARGV[3]
    )

    redis.call(
        "SET",
        KEYS[2],
        "1",
        "EX",
        ARGV[4]
    )

    return 1
    """

    def __init__(self, redis_client, prefix="payment:metrics"):
        self.redis = redis_client
        self.prefix = prefix.rstrip(":")
        self.retention_seconds = WINDOWS_SECONDS["60m"]

    def _key(self, gateway):
        return f"{self.prefix}:events:{gateway}"

    def _deduplication_key(self, event_id):
        return f"{self.prefix}:processed:{event_id}"

    @staticmethod
    def _normalize_gateway(gateway: str) -> str:
        normalized = str(gateway or "").strip().lower()

        if not normalized:
            raise ValueError("Gateway cannot be empty")

        if len(normalized) > 100:
            raise ValueError("Gateway name is too long")

        return normalized

    async def record(self, event: dict) -> bool:
        """
        Record a gateway attempt exactly once.

        Returns:
            True: event was newly recorded.
            False: event was already processed.
        """
        if not isinstance(event, dict):
            raise ValueError("Event must be a dictionary")

        required = {
            "event_id",
            "gateway",
            "outcome",
            "latency_ms",
        }

        if not required.issubset(event):
            raise ValueError("Event is missing required fields")

        raw_event_id = event["event_id"]

        if raw_event_id is None:
            raise ValueError("event_id cannot be empty")

        event_id = str(raw_event_id).strip()

        if not event_id:
            raise ValueError("event_id cannot be empty")

        if len(event_id) > 128:
            raise ValueError("event_id is too long")

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
            raise ValueError(
                "Latency must be finite and non-negative"
            )

        current_timestamp = time.time()
        occurred_at = event.get("occurred_at")

        if occurred_at:
            if not isinstance(occurred_at, str):
                raise ValueError("occurred_at must be a string")

            try:
                parsed_datetime = datetime.fromisoformat(
                    occurred_at.replace("Z", "+00:00")
                )
            except ValueError:
                raise ValueError(
                    "Invalid occurred_at timestamp"
                ) from None

            if parsed_datetime.tzinfo is None:
                raise ValueError(
                    "occurred_at must include timezone information"
                )

            event_timestamp = parsed_datetime.timestamp()

        else:
            event_timestamp = current_timestamp
            occurred_at = datetime.fromtimestamp(
                event_timestamp,
                timezone.utc,
            ).isoformat()

        if not math.isfinite(event_timestamp):
            raise ValueError("Invalid event timestamp")

        # Reject timestamps that are too far ahead of the service clock.
        if event_timestamp > current_timestamp + 300:
            raise ValueError(
                "Event timestamp is more than five minutes in the future"
            )

        normalized = {
            **event,
            "event_id": event_id,
            "gateway": gateway,
            "outcome": outcome,
            "latency_ms": latency,
            "payment_method": str(
                event.get("payment_method") or "UNKNOWN"
            ).strip().upper(),
            "bank_code": str(
                event.get("bank_code") or "UNKNOWN"
            ).strip().upper(),
            "occurred_at": occurred_at,
        }

        member = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
        )

        event_key = self._key(gateway)
        deduplication_key = self._deduplication_key(event_id)

        # Cleanup must use the current time, not the event timestamp.
        # This also cleans stale records when delayed events arrive.
        retention_cutoff = (
            current_timestamp - self.retention_seconds
        )

        result = await self.redis.eval(
            self.RECORD_EVENT_SCRIPT,
            2,
            event_key,
            deduplication_key,
            event_timestamp,
            member,
            retention_cutoff,
            self.retention_seconds,
        )

        return bool(result)

    async def get_metrics(
        self,
        gateway: str,
        window: str,
    ) -> dict:
        if window not in WINDOWS_SECONDS:
            raise ValueError("Unsupported metrics window")

        gateway = self._normalize_gateway(gateway)

        current_timestamp = time.time()
        cutoff = current_timestamp - WINDOWS_SECONDS[window]

        # Exclude future-dated records from current metrics.
        members = await self.redis.zrangebyscore(
            self._key(gateway),
            cutoff,
            current_timestamp,
        )

        events = [
            json.loads(
                member.decode("utf-8")
                if isinstance(member, bytes)
                else member
            )
            for member in members
        ]

        latencies = [
            event["latency_ms"]
            for event in events
        ]

        success_count = sum(
            event["outcome"] == "success"
            for event in events
        )

        failure_count = sum(
            event["outcome"] == "failure"
            for event in events
        )

        timeout_count = sum(
            event["outcome"] == "timeout"
            for event in events
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
                round(success_count / total, 4)
                if total
                else 0.0
            ),
            "p50_latency_ms": percentile(latencies, 0.50),
            "p95_latency_ms": percentile(latencies, 0.95),
            "p99_latency_ms": percentile(latencies, 0.99),
        }

    async def get_all_metrics(self) -> dict:
        gateways = set()

        async for key in self.redis.scan_iter(
            match=f"{self.prefix}:events:*"
        ):
            if isinstance(key, bytes):
                key = key.decode("utf-8")

            gateway = key.removeprefix(
                f"{self.prefix}:events:"
            )

            if gateway:
                gateways.add(gateway)

        result = {}

        for gateway in sorted(gateways):
            result[gateway] = {}

            for window in WINDOWS_SECONDS:
                result[gateway][window] = await self.get_metrics(
                    gateway,
                    window,
                )

        return result
