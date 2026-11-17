
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class GatewayMetrics:
    success_rate: float = 0.95
    p95_latency_ms: float = 1000.0

    bank_affinity: dict[str, float] = field(default_factory=dict)
    method_affinity: dict[str, float] = field(default_factory=dict)

    min_amount_minor: int | None = None
    max_amount_minor: int | None = None

    circuit_open: bool = False


@dataclass
class GatewayScore:
    provider: str
    score: float
    available: bool
    breakdown: dict[str, float]


class ScoringEngine:
    """
    Ranks payment gateways using Redis performance telemetry
    and payment-specific context.

    Small samples are smoothed toward safe baseline values.
    Invalid telemetry is ignored, and Redis failures preserve
    the last known good metrics.
    """

    WEIGHTS = {
        "success_rate": 0.35,
        "latency": 0.25,
        "bank_affinity": 0.15,
        "method_affinity": 0.10,
        "amount_fit": 0.10,
        "availability": 0.05,
    }

    MAX_LATENCY_MS = 2000.0
    DEFAULT_SUCCESS_RATE = 0.95
    DEFAULT_LATENCY_MS = 1000.0
    DEFAULT_AFFINITY = 0.5

    METRICS_WINDOW_SECONDS = 300
    REDIS_KEY_PREFIX = "payment:metrics:events:"
    PRIOR_SAMPLE_SIZE = 5

    def __init__(
        self,
        metrics: dict[str, GatewayMetrics] | None = None,
    ):
        self.metrics = metrics if metrics is not None else {}

    @staticmethod
    def _normalize_provider(provider: Any) -> str:
        return str(provider).strip().lower()

    @staticmethod
    def _clamp(value: float) -> float:
        if not math.isfinite(value):
            return 0.0

        return max(0.0, min(1.0, value))

    @staticmethod
    def _decode_event(member: Any) -> dict | None:
        try:
            if isinstance(member, bytes):
                member = member.decode("utf-8")

            if not isinstance(member, str):
                return None

            event = json.loads(member)

            return event if isinstance(event, dict) else None

        except (TypeError, ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def _percentile(
        values: list[float],
        percentile: float,
    ) -> float:
        if not values:
            return ScoringEngine.DEFAULT_LATENCY_MS

        ordered = sorted(values)

        index = max(
            0,
            math.ceil(percentile * len(ordered)) - 1,
        )

        return ordered[index]

    @staticmethod
    def _smooth_rate(
        observed_successes: int,
        observed_total: int,
        baseline: float,
        prior_samples: int,
    ) -> float:
        if observed_total <= 0:
            return baseline

        weighted_successes = (
            observed_successes + baseline * prior_samples
        )

        total_samples = observed_total + prior_samples

        return weighted_successes / total_samples

    @staticmethod
    def _group_success_rates(
        events: list[dict],
        field_name: str,
    ) -> dict[str, float]:
        grouped: dict[str, list[int]] = {}

        for event in events:
            key = str(
                event.get(field_name, "UNKNOWN")
            ).strip().upper()

            if not key or key == "UNKNOWN":
                continue

            if key not in grouped:
                grouped[key] = [0, 0]

            grouped[key][1] += 1

            if event.get("outcome") == "success":
                grouped[key][0] += 1

        return {
            key: ScoringEngine._smooth_rate(
                observed_successes=successes,
                observed_total=total,
                baseline=ScoringEngine.DEFAULT_AFFINITY,
                prior_samples=ScoringEngine.PRIOR_SAMPLE_SIZE,
            )
            for key, (successes, total) in grouped.items()
            if total > 0
        }

    def _get_existing_metrics(
        self,
        provider: str,
    ) -> GatewayMetrics:
        normalized = self._normalize_provider(provider)

        for key, metrics in self.metrics.items():
            if self._normalize_provider(key) == normalized:
                return metrics

        return GatewayMetrics()

    async def refresh_from_redis(
        self,
        redis_client,
        providers,
    ) -> None:
        """
        Refresh gateway metrics from the latest five minutes
        of Redis telemetry events.

        Redis failures preserve existing metrics.
        Invalid events never influence scoring.
        """

        if redis_client is None:
            return

        cutoff = time.time() - self.METRICS_WINDOW_SECONDS

        for provider in providers:
            normalized_provider = self._normalize_provider(provider)

            if not normalized_provider:
                continue

            existing = self._get_existing_metrics(provider)

            try:
                key = (
                    f"{self.REDIS_KEY_PREFIX}"
                    f"{normalized_provider}"
                )

                members = await redis_client.zrangebyscore(
                    key,
                    cutoff,
                    "+inf",
                )

                events = []

                for member in members:
                    event = self._decode_event(member)

                    if event is None:
                        continue

                    event_gateway = self._normalize_provider(
                        event.get("gateway", "")
                    )

                    if event_gateway and event_gateway != normalized_provider:
                        continue

                    outcome = str(
                        event.get("outcome", "")
                    ).strip().lower()

                    if outcome not in {
                        "success",
                        "failure",
                        "timeout",
                    }:
                        continue

                    raw_latency = event.get("latency_ms")

                    if isinstance(raw_latency, bool):
                        continue

                    try:
                        latency = float(raw_latency)
                    except (TypeError, ValueError):
                        continue

                    if not math.isfinite(latency) or latency < 0:
                        continue

                    event["outcome"] = outcome
                    event["latency_ms"] = latency

                    events.append(event)

                if not events:
                    self.metrics[provider] = GatewayMetrics(
                        min_amount_minor=existing.min_amount_minor,
                        max_amount_minor=existing.max_amount_minor,
                        circuit_open=existing.circuit_open,
                    )
                    continue

                successes = sum(
                    event["outcome"] == "success"
                    for event in events
                )

                sample_count = len(events)

                observed_success_rate = self._smooth_rate(
                    observed_successes=successes,
                    observed_total=sample_count,
                    baseline=self.DEFAULT_SUCCESS_RATE,
                    prior_samples=self.PRIOR_SAMPLE_SIZE,
                )

                observed_p95_latency = self._percentile(
                    [
                        event["latency_ms"]
                        for event in events
                    ],
                    0.95,
                )

                smoothed_latency = (
                    observed_p95_latency * sample_count
                    + self.DEFAULT_LATENCY_MS * self.PRIOR_SAMPLE_SIZE
                ) / (
                    sample_count + self.PRIOR_SAMPLE_SIZE
                )

                self.metrics[provider] = GatewayMetrics(
                    success_rate=self._clamp(
                        observed_success_rate
                    ),
                    p95_latency_ms=max(
                        0.0,
                        smoothed_latency,
                    ),
                    bank_affinity=self._group_success_rates(
                        events,
                        "bank_code",
                    ),
                    method_affinity=self._group_success_rates(
                        events,
                        "payment_method",
                    ),
                    min_amount_minor=existing.min_amount_minor,
                    max_amount_minor=existing.max_amount_minor,
                    circuit_open=existing.circuit_open,
                )

            except Exception:
                logger.warning(
                    "Could not refresh Redis metrics for gateway %s",
                    provider,
                    exc_info=True,
                )

    def _get_metrics(self, provider: str) -> GatewayMetrics:
        return self._get_existing_metrics(provider)

    def _affinity_score(
        self,
        affinities: dict[str, float],
        key: str | None,
    ) -> float:
        if not key:
            return self.DEFAULT_AFFINITY

        return self._clamp(
            affinities.get(
                str(key).strip().upper(),
                self.DEFAULT_AFFINITY,
            )
        )

    def _amount_score(
        self,
        amount_minor: int,
        metrics: GatewayMetrics,
    ) -> float:
        minimum = metrics.min_amount_minor
        maximum = metrics.max_amount_minor

        if minimum is None and maximum is None:
            return self.DEFAULT_AFFINITY

        if minimum is not None and amount_minor < minimum:
            return 0.0

        if maximum is not None and amount_minor > maximum:
            return 0.0

        return 1.0

    def score(
        self,
        provider: str,
        payment: Any,
    ) -> GatewayScore:
        metrics = self._get_metrics(provider)

        available = not metrics.circuit_open

        latency = metrics.p95_latency_ms

        if not math.isfinite(latency) or latency < 0:
            latency = self.DEFAULT_LATENCY_MS

        latency_score = self._clamp(
            1.0 - (latency / self.MAX_LATENCY_MS)
        )

        amount_minor = getattr(
            payment,
            "amount_minor",
            0,
        )

        if (
            isinstance(amount_minor, bool)
            or not isinstance(amount_minor, int)
            or amount_minor < 0
        ):
            amount_minor = 0

        breakdown = {
            "success_rate": self._clamp(
                metrics.success_rate
            ),
            "latency": latency_score,
            "bank_affinity": self._affinity_score(
                metrics.bank_affinity,
                getattr(payment, "bank_code", None),
            ),
            "method_affinity": self._affinity_score(
                metrics.method_affinity,
                getattr(payment, "payment_method", None),
            ),
            "amount_fit": self._amount_score(
                amount_minor,
                metrics,
            ),
            "availability": 1.0 if available else 0.0,
        }

        total_score = sum(
            breakdown[name] * weight
            for name, weight in self.WEIGHTS.items()
        )

        if not available:
            total_score = 0.0

        return GatewayScore(
            provider=provider,
            score=round(total_score, 4),
            available=available,
            breakdown={
                key: round(value, 4)
                for key, value in breakdown.items()
            },
        )

    def rank(
        self,
        adapters: dict[str, Any],
        payment: Any,
    ) -> list[GatewayScore]:
        scores = [
            self.score(provider, payment)
            for provider in adapters
        ]

        return sorted(
            scores,
            key=lambda item: (
                item.available,
                item.score,
                item.provider,
            ),
            reverse=True,
        )
