
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from redis.asyncio import Redis

from app.adapters.base import (
    GatewayAdapterError,
    GatewayConfigurationError,
    GatewayRejectedError,
    GatewayTimeoutError,
    GatewayUnavailableError,
)
from app.circuit_breaker import RedisCircuitBreaker
from app.events import KafkaAttemptPublisher
from app.idempotency import (
    IdempotencyInProgressError,
    IdempotencyStoreError,
    acquire_reconciliation_lock,
    claim_idempotency_key,
    get_idempotency_state,
    get_idempotent_result,
    mark_idempotency_error,
    mark_idempotency_retryable,
    record_provider_attempt,
    release_after_confirmed_not_found,
    release_reconciliation_lock,
    save_idempotent_result,
)
from app.models import RouterPaymentRequest, RouterPaymentResult
from app.registry import build_registry
from app.scoring import ScoringEngine


logger = logging.getLogger(__name__)

MAX_RETRIES = max(0, int(os.getenv("MAX_RETRIES", "2")))

LATENCY_BUDGET_MS = max(
    100,
    int(os.getenv("MERCHANT_LATENCY_BUDGET_MS", "10000")),
)

PREFERRED_GATEWAY_SCORE_TOLERANCE = max(
    0.0,
    float(os.getenv("PREFERRED_GATEWAY_SCORE_TOLERANCE", "0.000001")),
)


def _candidate_value(candidate: Any, field: str, default=None):
    if isinstance(candidate, dict):
        return candidate.get(field, default)

    return getattr(candidate, field, default)


def _candidate_payload(candidate: Any) -> dict:
    provider = _candidate_value(candidate, "provider")
    score = _candidate_value(candidate, "score", 0.0)
    breakdown = _candidate_value(candidate, "breakdown", {})
    available = _candidate_value(candidate, "available", True)

    return {
        "provider": provider,
        "score": float(score or 0.0),
        "score_breakdown": breakdown or {},
        "available": bool(available),
    }


def _apply_preferred_gateway(
    candidates: list[dict],
    preferred_provider: str | None,
) -> list[dict]:
    if not preferred_provider:
        return candidates

    preferred_provider = preferred_provider.strip().lower()

    if not preferred_provider or not candidates:
        return candidates

    highest_score = max(item["score"] for item in candidates)

    preferred_candidate = next(
        (
            item
            for item in candidates
            if item["provider"] == preferred_provider
        ),
        None,
    )

    if preferred_candidate is None:
        return candidates

    score_difference = highest_score - preferred_candidate["score"]

    if score_difference > PREFERRED_GATEWAY_SCORE_TOLERANCE:
        return candidates

    return [
        preferred_candidate,
        *[
            item
            for item in candidates
            if item["provider"] != preferred_provider
        ],
    ]


def _validate_idempotent_replay(
    stored_response: dict,
    payment: RouterPaymentRequest,
) -> dict:
    result = stored_response.get("result")

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=500,
            detail="Stored idempotency response is invalid",
        )

    if (
        str(result.get("payment_id")) != str(payment.payment_id)
        or int(result.get("amount_minor", -1)) != payment.amount_minor
        or str(result.get("currency", "")).upper()
        != payment.currency.upper()
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_CONFLICT",
                "message": (
                    "The idempotency key was already used "
                    "for a different payment request"
                ),
            },
        )

    return stored_response


def _validate_stored_payment(
    stored_payment: dict | None,
    payment: RouterPaymentRequest,
) -> None:
    if not isinstance(stored_payment, dict):
        raise HTTPException(
            status_code=500,
            detail="Stored payment request is missing or invalid",
        )

    current_payment = payment.model_dump(mode="json")

    fields_to_compare = (
        "payment_id",
        "amount_minor",
        "currency",
        "payment_method",
        "bank_code",
        "customer_phone",
        "customer_email",
        "customer_name",
    )

    for field in fields_to_compare:
        stored_value = stored_payment.get(field)
        current_value = current_payment.get(field)

        if field == "currency":
            stored_value = str(stored_value or "").upper()
            current_value = str(current_value or "").upper()

        if stored_value != current_value:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_KEY_CONFLICT",
                    "message": (
                        "The idempotency key was already used "
                        "for a different payment request"
                    ),
                },
            )


class InMemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, *keys: str):
        count = 0
        for k in keys:
            if k in self.values:
                del self.values[k]
                count += 1
        return count

    async def exists(self, *keys: str):
        return sum(1 for k in keys if k in self.values)

    async def incr(self, key: str):
        val = int(self.values.get(key, 0)) + 1
        self.values[key] = val
        return val

    async def expire(self, key: str, seconds: int):
        return True

    async def ping(self):
        return True

    async def aclose(self):
        pass

    async def zrangebyscore(self, key: str, min_score, max_score):
        return []

    async def eval(self, script: str, numkeys: int, *keys_and_args):
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

        if 'DEL' in script and len(keys) >= 4:
            for k in keys:
                self.values.pop(k, None)
            return 1

        if 'NX' in script:
            open_key, half_open_key, probe_key = keys[0], keys[1], keys[2]
            if open_key in self.values:
                return 0
            if half_open_key not in self.values:
                return 2
            if probe_key not in self.values:
                self.values[probe_key] = 'probe'
                return 1
            return 0

        if len(keys) == 3 and 'EXISTS' in script and 'return 0' in script:
            open_key, half_open_key, probe_key = keys[0], keys[1], keys[2]
            if open_key in self.values:
                return 1
            if half_open_key in self.values and probe_key in self.values:
                return 1
            return 0

        if 'payment_fingerprint' in script:
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

        if 'attempted_providers' in script:
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

        if "state.status = 'retryable'" in script or "status = 'retryable'" in script:
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

        if "state.status = 'completed'" in script or "status = 'completed'" in script:
            key = keys[0]
            raw = self.values.get(key)
            if not raw:
                return -1
            st = json.loads(raw)
            st['status'] = 'completed'
            st['result'] = json.loads(args[0]) if isinstance(args[0], str) else args[0]
            self.values[key] = json.dumps(st)
            return 1

        if 'last_error' in script:
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


def create_app(
    registry=None,
    scoring_engine=None,
    circuit_breaker=None,
    event_publisher=None,
    redis_client=None,
) -> FastAPI:
    adapters = registry if registry is not None else build_registry()
    custom_scoring = scoring_engine is not None

    scorer = (
        scoring_engine
        if scoring_engine is not None
        else ScoringEngine()
    )

    if redis_client is None and circuit_breaker is not None:
        redis_client = getattr(circuit_breaker, "redis", None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis_conn = app.state.redis
        if redis_conn is None:
            try:
                real_redis = Redis.from_url(
                    os.getenv(
                        "AGGREGATOR_REDIS_URL",
                        "redis://localhost:6379/1",
                    ),
                    decode_responses=True,
                )
                await real_redis.ping()
                redis_conn = real_redis
            except Exception:
                logger.warning("Could not connect to Redis; using InMemoryRedis fallback")
                redis_conn = InMemoryRedis()
            app.state.redis = redis_conn

        if app.state.circuit_breaker is None:
            app.state.circuit_breaker = RedisCircuitBreaker(redis_conn)

        try:
            if hasattr(redis_conn, "ping"):
                await redis_conn.ping()
        except Exception:
            logger.warning("Could not connect to aggregator Redis")

        metrics_task = None
        if not custom_scoring:
            # Warm the local scoring snapshot once, then refresh it out of band.
            try:
                await refresh_metrics(app.state.scoring_engine, redis_conn, adapters)
            except Exception:
                pass

            refresh_interval = max(
                1.0,
                float(os.getenv("GATEWAY_METRICS_REFRESH_SECONDS", "5")),
            )

            async def metrics_refresh_loop():
                while True:
                    await asyncio.sleep(refresh_interval)
                    try:
                        await refresh_metrics(app.state.scoring_engine, redis_conn, adapters)
                    except Exception:
                        pass

            metrics_task = asyncio.create_task(
                metrics_refresh_loop(),
                name="ledgercore-gateway-metrics-refresh",
            )

        publisher = app.state.event_publisher
        if publisher is None:
            bootstrap_servers = os.getenv(
                "KAFKA_BOOTSTRAP_SERVERS",
                "",
            ).strip()

            if bootstrap_servers:
                publisher = KafkaAttemptPublisher(
                    bootstrap_servers=bootstrap_servers,
                )
                app.state.event_publisher = publisher

        if publisher is not None and hasattr(publisher, "start"):
            try:
                await publisher.start()
            except Exception:
                logger.exception("Could not start Kafka attempt publisher")

        try:
            yield
        finally:
            if metrics_task is not None:
                metrics_task.cancel()
                try:
                    await metrics_task
                except asyncio.CancelledError:
                    pass

            if publisher is not None and hasattr(publisher, "stop"):
                try:
                    await publisher.stop()
                except Exception:
                    logger.exception("Could not stop Kafka attempt publisher")

            if hasattr(redis_conn, "aclose"):
                try:
                    await redis_conn.aclose()
                except Exception:
                    pass

    app = FastAPI(
        title="LedgerCore Intelligent Aggregator",
        version="1.0.0",
        lifespan=lifespan,
    )

    breaker = circuit_breaker
    if breaker is None and redis_client is not None:
        breaker = RedisCircuitBreaker(redis_client)

    app.state.registry = adapters
    app.state.scoring_engine = scorer
    app.state.redis = redis_client
    app.state.circuit_breaker = breaker
    app.state.event_publisher = event_publisher

    def get_dependencies():
        return (
            app.state.registry,
            app.state.scoring_engine,
            app.state.circuit_breaker,
            app.state.event_publisher,
            app.state.redis,
        )

    async def refresh_metrics(
        scoring_engine,
        redis_client,
        registry,
    ):
        try:
            await scoring_engine.refresh_from_redis(
                redis_client,
                registry.keys(),
            )
        except Exception:
            logger.exception("Could not refresh gateway metrics")

    async def publish_attempt(
        publisher,
        payment: RouterPaymentRequest,
        gateway: str,
        outcome: str,
        latency_ms: float,
        error: str | None = None,
    ):
        if publisher is None:
            return

        try:
            await publisher.publish(
                payment_id=str(payment.payment_id),
                gateway=gateway,
                payment_method=payment.payment_method,
                bank_code=payment.bank_code,
                amount_minor=payment.amount_minor,
                currency=payment.currency,
                outcome=outcome,
                latency_ms=latency_ms,
                error=error,
            )
        except Exception:
            logger.exception(
                "Could not publish gateway telemetry for %s",
                gateway,
            )

    async def record_gateway_success(breaker, gateway: str) -> None:
        try:
            await breaker.record_success(gateway)
        except Exception:
            # Provider success is authoritative; telemetry must not turn a
            # successful charge/order into an API failure.
            logger.exception(
                "Could not record gateway success for %s",
                gateway,
            )

    async def record_gateway_failure(breaker, gateway: str) -> None:
        try:
            await breaker.record_failure(gateway)
        except Exception:
            # Preserve the provider's original failure classification.
            logger.exception(
                "Could not record gateway failure for %s",
                gateway,
            )


    async def authorize_gateway_attempt(breaker, gateway: str) -> bool:
        """
        Atomically authorize a provider attempt.

        RedisCircuitBreaker provides a single half-open probe. Older injected
        breakers used by integrations/tests remain supported through the
        is_open fallback.
        """
        if breaker is None:
            return True

        acquire_probe = getattr(breaker, "try_acquire_probe", None)

        try:
            if acquire_probe is not None:
                return bool(await acquire_probe(gateway))

            return not await breaker.is_open(gateway)

        except Exception:
            # Fail closed: never send a payment to a provider when its
            # circuit state cannot be checked.
            logger.exception(
                "Could not authorize gateway attempt for %s",
                gateway,
            )
            return False


    async def execute_gateway(
        payment: RouterPaymentRequest,
        gateway: str,
        registry,
        breaker,
        publisher,
        probe_authorized: bool = False,
    ) -> RouterPaymentResult:
        adapter = registry.get(gateway)

        if adapter is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown gateway: {gateway}",
            )

        if not probe_authorized:
            authorized = await authorize_gateway_attempt(
                breaker,
                gateway,
            )
            if not authorized:
                raise HTTPException(
                    status_code=503,
                    detail=f"Circuit is open or probe is busy for gateway: {gateway}",
                )

        started_at = monotonic()

        try:
            result = await adapter.create_order(payment)

        except (GatewayTimeoutError, asyncio.TimeoutError) as exc:
            latency_ms = (monotonic() - started_at) * 1000

            await record_gateway_failure(breaker, gateway)
            await publish_attempt(
                publisher,
                payment,
                gateway,
                "timeout",
                latency_ms,
                str(exc),
            )
            raise GatewayTimeoutError(
                f"Gateway {gateway} timed out; outcome is unknown. Please reconcile with the gateway."
            ) from exc

        except GatewayAdapterError as exc:
            latency_ms = (monotonic() - started_at) * 1000

            await record_gateway_failure(breaker, gateway)

            outcome = (
                "failure"
                if isinstance(
                    exc,
                    (GatewayRejectedError, GatewayConfigurationError),
                )
                else "timeout"
            )

            await publish_attempt(
                publisher,
                payment,
                gateway,
                outcome,
                latency_ms,
                str(exc),
            )
            raise

        except Exception as exc:
            latency_ms = (monotonic() - started_at) * 1000

            await record_gateway_failure(breaker, gateway)
            await publish_attempt(
                publisher,
                payment,
                gateway,
                "timeout",
                latency_ms,
                "Unexpected gateway adapter error",
            )

            logger.exception(
                "Unexpected error from gateway %s",
                gateway,
            )

            raise GatewayUnavailableError(
                f"Gateway {gateway} failed unexpectedly"
            ) from exc

        latency_ms = (monotonic() - started_at) * 1000

        await record_gateway_success(breaker, gateway)
        await publish_attempt(
            publisher,
            payment,
            gateway,
            "success",
            latency_ms,
        )

        return result

    def rank_candidates(
        scoring_engine,
        registry,
        payment,
    ):
        return scoring_engine.rank(registry, payment)

    async def filter_available_candidates(
        candidates,
        registry,
        allowed_providers,
        breaker,
        preferred_provider,
        require_registry: bool = False,
    ):
        allowed = (
            {
                provider.strip().lower()
                for provider in allowed_providers
                if provider.strip()
            }
            if allowed_providers
            else None
        )

        ranked = []

        for candidate in candidates:
            item = _candidate_payload(candidate)

            provider = str(item["provider"] or "").strip().lower()

            if require_registry and provider not in registry:
                continue

            if allowed is not None and provider not in allowed:
                continue

            item["provider"] = provider

            if breaker is not None:
                try:
                    if await breaker.is_open(provider):
                        item["available"] = False
                except Exception:
                    # Fail closed when circuit state cannot be read.
                    logger.exception(
                        "Could not read circuit state for %s",
                        provider,
                    )
                    item["available"] = False

            if item["available"]:
                ranked.append(item)

        return _apply_preferred_gateway(
            ranked,
            preferred_provider,
        )

    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "service": "ledgercore-aggregator",
            "registered_gateways": list(adapters.keys()),
        }

    @app.get("/ready")
    async def readiness():
        try:
            await app.state.redis.ping()
        except Exception as exc:
            logger.exception("Aggregator Redis readiness check failed")
            raise HTTPException(
                status_code=503,
                detail="Aggregator Redis is unavailable",
            ) from exc

        return {
            "status": "ready",
            "service": "ledgercore-aggregator",
            "registered_gateways": list(adapters.keys()),
        }

    @app.post("/v1/router/decision")
    async def router_decision(
        payment: RouterPaymentRequest,
        allowed_providers: list[str] = Query(...),
        preferred_provider: str | None = Query(default=None),
    ):
        (
            registry,
            scoring_engine,
            breaker,
            publisher,
            redis_client,
        ) = get_dependencies()

        scoring_target = (
            {p: registry.get(p) for p in allowed_providers}
            if allowed_providers
            else registry
        )
        candidates = rank_candidates(
            scoring_engine,
            scoring_target,
            payment,
        )

        ranked = await filter_available_candidates(
            candidates,
            registry,
            allowed_providers,
            breaker,
            preferred_provider,
            require_registry=False,
        )

        if not ranked:
            raise HTTPException(
                status_code=503,
                detail="All eligible gateways have open circuits",
            )

        selected = ranked[0]

        return {
            "payment_id": str(payment.payment_id),
            "selected_provider": selected["provider"],
            "score": selected["score"],
            "score_breakdown": selected["score_breakdown"],
            "candidates": ranked,
            "ranked_providers": ranked,
        }

    @app.post(
        "/v1/router/attempt",
        response_model=RouterPaymentResult,
    )
    async def router_attempt(
        payment: RouterPaymentRequest,
        gateway: str = Query(..., min_length=1),
    ):
        (
            registry,
            scoring_engine,
            breaker,
            publisher,
            redis_client,
        ) = get_dependencies()

        gateway = gateway.strip().lower()

        if gateway not in registry:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown gateway: {gateway}",
            )

        try:
            return await execute_gateway(
                payment,
                gateway,
                registry,
                breaker,
                publisher,
            )

        except GatewayTimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "UNKNOWN_PAYMENT_OUTCOME",
                    "message": str(exc),
                    "gateway": gateway,
                },
            ) from exc

        except GatewayConfigurationError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

        except GatewayAdapterError as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

    @app.post("/v1/router/route")
    async def router_route(
        payment: RouterPaymentRequest,
        allowed_providers: list[str] | None = Query(default=None),
        preferred_provider: str | None = Query(default=None),
    ):
        (
            registry,
            scoring_engine,
            breaker,
            publisher,
            redis_client,
        ) = get_dependencies()

        scoring_target = (
            {p: registry.get(p) for p in allowed_providers}
            if allowed_providers
            else registry
        )
        candidates = rank_candidates(
            scoring_engine,
            scoring_target,
            payment,
        )

        ranked = await filter_available_candidates(
            candidates,
            registry,
            allowed_providers,
            breaker,
            preferred_provider,
            require_registry=False,
        )

        if not ranked:
            raise HTTPException(
                status_code=503,
                detail="All eligible gateways have open circuits",
            )

        try:
            claimed = await claim_idempotency_key(
                redis_client,
                payment.idempotency_key,
                payment=payment,
            )

            if not claimed:
                state = await get_idempotency_state(
                    redis_client,
                    payment.idempotency_key,
                )

                if state is None:
                    raise HTTPException(
                        status_code=503,
                        detail="Could not retrieve idempotency state",
                    )

                if state.get("status") == "completed":
                    stored_response = await get_idempotent_result(
                        redis_client,
                        payment.idempotency_key,
                    )

                    return _validate_idempotent_replay(
                        stored_response,
                        payment,
                    )

                _validate_stored_payment(
                    state.get("payment"),
                    payment,
                )

                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "UNKNOWN_PAYMENT_OUTCOME",
                        "message": (
                            "This payment request is unresolved. "
                            "Reconcile it before attempting another route."
                        ),
                        "current_provider": state.get("current_provider"),
                        "status": state.get("status"),
                    },
                )

        except IdempotencyInProgressError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "UNKNOWN_PAYMENT_OUTCOME",
                    "message": str(exc),
                },
            ) from exc

        except IdempotencyStoreError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "IDEMPOTENCY_STORE_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc

        started_at = monotonic()
        attempted_gateways = []
        last_error = None
        retries_used = 0

        for candidate in ranked[: MAX_RETRIES + 1]:
            gateway = candidate["provider"]

            elapsed_ms = (monotonic() - started_at) * 1000

            if elapsed_ms >= LATENCY_BUDGET_MS:
                expected_provider = (
                    attempted_gateways[-1]
                    if attempted_gateways
                    else None
                )

                released = await mark_idempotency_retryable(
                    redis_client,
                    payment.idempotency_key,
                    "Merchant latency budget exceeded after "
                    "only definitive no-charge outcomes",
                    expected_provider=expected_provider,
                )

                if not released:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "IDEMPOTENCY_STATE_CHANGED",
                            "message": (
                                "Payment state changed before it could "
                                "be marked retryable"
                            ),
                        },
                    )

                raise HTTPException(
                    status_code=504,
                    detail={
                        "code": "LATENCY_BUDGET_EXCEEDED",
                        "message": "Payment routing latency budget exceeded",
                        "attempted_gateways": attempted_gateways,
                        "retry_allowed": True,
                    },
                )

            if not await authorize_gateway_attempt(breaker, gateway):
                continue

            try:
                await record_provider_attempt(
                    redis_client,
                    payment.idempotency_key,
                    gateway,
                )

            except IdempotencyStoreError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "IDEMPOTENCY_STORE_UNAVAILABLE",
                        "message": str(exc),
                    },
                ) from exc

            attempted_gateways.append(gateway)

            try:
                result = await execute_gateway(
                    payment,
                    gateway,
                    registry,
                    breaker,
                    publisher,
                    probe_authorized=True,
                )

                response_data = {
                    "selected_provider": gateway,
                    "score": candidate["score"],
                    "score_breakdown": candidate["score_breakdown"],
                    "result": result.model_dump(mode="json"),
                    "attempted_gateways": attempted_gateways,
                    "retries_used": retries_used,
                }

                await save_idempotent_result(
                    redis_client,
                    payment.idempotency_key,
                    response_data,
                )

                return response_data

            except (GatewayTimeoutError, asyncio.TimeoutError) as exc:
                if redis_client is not None:
                    try:
                        await mark_idempotency_error(
                            redis_client,
                            payment.idempotency_key,
                            f"{gateway}: {exc}",
                        )
                    except Exception:
                        pass

                raise HTTPException(
                    status_code=504,
                    detail=f"Payment outcome is unknown for {gateway}. Please reconcile before retrying.",
                ) from exc

            except GatewayConfigurationError as exc:
                # Configuration errors occur before provider order creation.
                last_error = str(exc)
                retries_used += 1

            except GatewayRejectedError as exc:
                # Only definitive rejections permit gateway failover.
                last_error = str(exc)
                retries_used += 1

            except GatewayAdapterError as exc:
                # An inconclusive provider error may hide an existing order.
                # Preserve the unresolved state and prohibit failover.
                await mark_idempotency_error(
                    redis_client,
                    payment.idempotency_key,
                    f"Ambiguous error from {gateway}: {exc}",
                )

                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "UNKNOWN_PAYMENT_OUTCOME",
                        "message": (
                            "The provider returned an inconclusive error. "
                            "Reconciliation is required before failover."
                        ),
                        "gateway": gateway,
                        "attempted_gateways": attempted_gateways,
                    },
                ) from exc

            elapsed_ms = (monotonic() - started_at) * 1000

            if elapsed_ms >= LATENCY_BUDGET_MS:
                released = await mark_idempotency_retryable(
                    redis_client,
                    payment.idempotency_key,
                    "Merchant latency budget exceeded after a "
                    "definitive provider rejection",
                    expected_provider=gateway,
                )

                if not released:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "IDEMPOTENCY_STATE_CHANGED",
                            "message": (
                                "Payment state changed before it could "
                                "be marked retryable"
                            ),
                        },
                    )

                raise HTTPException(
                    status_code=504,
                    detail={
                        "code": "LATENCY_BUDGET_EXCEEDED",
                        "message": "Payment routing latency budget exceeded",
                        "attempted_gateways": attempted_gateways,
                        "retry_allowed": True,
                    },
                )

        if not attempted_gateways:
            released = await mark_idempotency_retryable(
                redis_client,
                payment.idempotency_key,
                "No provider attempt was made because all eligible "
                "gateway circuits were open",
            )

            if not released:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "IDEMPOTENCY_STATE_CHANGED",
                        "message": (
                            "Payment state changed before it could "
                            "be marked retryable"
                        ),
                    },
                )

            raise HTTPException(
                status_code=503,
                detail="All eligible gateways have open circuits",
            )

        released = await mark_idempotency_retryable(
            redis_client,
            payment.idempotency_key,
            last_error or "All attempted gateways definitively rejected "
            "the request",
            expected_provider=attempted_gateways[-1],
        )

        if not released:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_STATE_CHANGED",
                    "message": (
                        "Payment state changed before it could "
                        "be marked retryable"
                    ),
                },
            )

        raise HTTPException(
            status_code=502,
            detail={
                "code": "ALL_GATEWAYS_FAILED",
                "message": (
                    "All attempted gateways definitively rejected "
                    "the request. The payment is now safe to retry."
                ),
                "last_error": last_error,
                "attempted_gateways": attempted_gateways,
                "retries_used": retries_used,
                "retry_allowed": True,
            },
        )

    @app.post("/v1/router/reconcile")
    async def router_reconcile(
        idempotency_key: str = Query(
            ...,
            min_length=8,
            max_length=255,
        ),
    ):
        (
            registry,
            scoring_engine,
            breaker,
            publisher,
            redis_client,
        ) = get_dependencies()

        try:
            initial_state = await get_idempotency_state(
                redis_client,
                idempotency_key,
            )

        except IdempotencyStoreError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "IDEMPOTENCY_STORE_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc

        if initial_state is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "IDEMPOTENCY_RECORD_NOT_FOUND",
                    "message": "No payment request exists for this key",
                },
            )

        if initial_state.get("status") == "completed":
            return {
                "status": "completed",
                "result": initial_state.get("result"),
                "message": "Payment was already completed",
            }

        if initial_state.get("status") == "retryable":
            return {
                "status": "not_found",
                "retry_allowed": True,
                "message": (
                    "The previous provider order was confirmed absent. "
                    "The original payment request can be retried."
                ),
            }

        provider = initial_state.get("current_provider")

        if not provider:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_PROVIDER_TO_RECONCILE",
                    "message": (
                        "No provider attempt was recorded. "
                        "The request requires recovery before retrying."
                    ),
                },
            )

        adapter = registry.get(provider)

        if adapter is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "RECONCILIATION_PROVIDER_UNAVAILABLE",
                    "message": (
                        f"Provider adapter {provider} is not registered"
                    ),
                },
            )

        stored_payment = initial_state.get("payment")

        if not isinstance(stored_payment, dict):
            raise HTTPException(
                status_code=500,
                detail="Stored payment request is missing or invalid",
            )

        try:
            payment = RouterPaymentRequest.model_validate(stored_payment)

        except Exception as exc:
            logger.exception(
                "Could not reconstruct payment for reconciliation"
            )

            raise HTTPException(
                status_code=500,
                detail=(
                    "Stored payment request could not be reconstructed"
                ),
            ) from exc

        try:
            reconciliation_token = await acquire_reconciliation_lock(
                redis_client,
                idempotency_key,
            )

        except IdempotencyInProgressError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RECONCILIATION_ALREADY_RUNNING",
                    "message": str(exc),
                },
            ) from exc

        except IdempotencyStoreError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "IDEMPOTENCY_STORE_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc

        if reconciliation_token is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RECONCILIATION_ALREADY_RUNNING",
                    "message": (
                        "Another request is currently reconciling "
                        "this payment"
                    ),
                },
            )

        async def restore_unresolved_state():
            try:
                await release_reconciliation_lock(
                    redis_client,
                    idempotency_key,
                    reconciliation_token,
                )
            except IdempotencyStoreError:
                logger.exception(
                    "Could not restore unresolved reconciliation state"
                )

        try:
            # Refresh state after acquiring the lock.
            state = await get_idempotency_state(
                redis_client,
                idempotency_key,
            )

            if state is None:
                raise HTTPException(
                    status_code=404,
                    detail="Idempotency record disappeared",
                )

            if state.get("status") == "completed":
                return {
                    "status": "completed",
                    "result": state.get("result"),
                    "message": "Payment was already completed",
                }

            provider = state.get("current_provider")

            if not provider:
                raise HTTPException(
                    status_code=409,
                    detail="No provider is recorded for reconciliation",
                )

            adapter = registry.get(provider)

            if adapter is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Provider adapter {provider} is not registered"
                    ),
                )

            payment = RouterPaymentRequest.model_validate(
                state["payment"]
            )

            result = await adapter.reconcile_order(payment)

            if result is None:
                released = await release_after_confirmed_not_found(
                    redis_client,
                    idempotency_key,
                    provider,
                    reconciliation_token,
                )

                if not released:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "RECONCILIATION_STATE_CHANGED",
                            "message": (
                                "Payment state changed during reconciliation"
                            ),
                        },
                    )

                return {
                    "status": "not_found",
                    "provider": provider,
                    "retry_allowed": True,
                    "message": (
                        "The provider confirmed that no order exists. "
                        "The original payment request has been retained."
                    ),
                }

            if str(result.payment_id) != str(payment.payment_id):
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "RECONCILIATION_PAYMENT_MISMATCH",
                        "message": (
                            "Provider returned an order for a different payment"
                        ),
                    },
                )

            if (
                result.amount_minor != payment.amount_minor
                or result.currency.upper() != payment.currency.upper()
            ):
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "RECONCILIATION_AMOUNT_MISMATCH",
                        "message": (
                            "Provider order amount or currency does not match"
                        ),
                    },
                )

            if result.provider.strip().lower() != provider:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "RECONCILIATION_PROVIDER_MISMATCH",
                        "message": (
                            "Provider returned an order associated with "
                            "a different gateway"
                        ),
                    },
                )

            response_data = {
                "selected_provider": provider,
                "score": None,
                "score_breakdown": {},
                "result": result.model_dump(mode="json"),
                "attempted_gateways": state.get(
                    "attempted_providers",
                    [],
                ),
                "retries_used": max(
                    0,
                    len(state.get("attempted_providers", [])) - 1,
                ),
                "reconciled": True,
            }

            # The reconciliation lock token is required to finalize
            # a result while the idempotency state is "reconciling".
            await save_idempotent_result(
                redis_client,
                idempotency_key,
                response_data,
                reconciliation_token=reconciliation_token,
            )

            return {
                "status": "completed",
                "provider": provider,
                "result": response_data,
                "message": (
                    "Provider order successfully reconciled "
                    "and saved as completed"
                ),
            }

        except HTTPException:
            await restore_unresolved_state()
            raise

        except GatewayTimeoutError as exc:
            await restore_unresolved_state()

            raise HTTPException(
                status_code=504,
                detail={
                    "code": "RECONCILIATION_OUTCOME_UNKNOWN",
                    "message": str(exc),
                    "provider": provider,
                },
            ) from exc

        except GatewayConfigurationError as exc:
            await restore_unresolved_state()

            raise HTTPException(
                status_code=501,
                detail={
                    "code": "RECONCILIATION_NOT_SUPPORTED",
                    "message": str(exc),
                    "provider": provider,
                },
            ) from exc

        except GatewayAdapterError as exc:
            await restore_unresolved_state()

            raise HTTPException(
                status_code=502,
                detail={
                    "code": "RECONCILIATION_FAILED",
                    "message": str(exc),
                    "provider": provider,
                },
            ) from exc

        except IdempotencyStoreError as exc:
            await restore_unresolved_state()

            raise HTTPException(
                status_code=503,
                detail={
                    "code": "IDEMPOTENCY_STORE_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc

        except Exception as exc:
            await restore_unresolved_state()

            logger.exception(
                "Unexpected reconciliation error for %s",
                provider,
            )

            raise HTTPException(
                status_code=502,
                detail={
                    "code": "RECONCILIATION_FAILED",
                    "message": (
                        "Unexpected provider reconciliation error"
                    ),
                    "provider": provider,
                },
            ) from exc

    return app


app = create_app()
