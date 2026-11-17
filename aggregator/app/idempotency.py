
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)

COMPLETED_TTL_SECONDS = 7 * 24 * 60 * 60
RECONCILIATION_LOCK_SECONDS = 60

KEY_PREFIX = "ledgercore:idempotency:"


class IdempotencyInProgressError(Exception):
    """The request exists but its payment outcome is unresolved."""


class IdempotencyConflictError(Exception):
    """The idempotency key was reused for another payment request."""


class IdempotencyStoreError(Exception):
    """Redis could not safely process an idempotency operation."""


def _state_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(
        idempotency_key.encode("utf-8")
    ).hexdigest()
    return f"{KEY_PREFIX}{digest}"


def _serialize(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        value = value.dict()

    return json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _decode(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None

    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise IdempotencyStoreError(
            "Stored idempotency record is invalid"
        ) from exc

    if not isinstance(value, dict):
        raise IdempotencyStoreError(
            "Stored idempotency record has an invalid format"
        )

    return value


def _payment_payload(payment: Any) -> dict[str, Any]:
    if hasattr(payment, "model_dump"):
        return payment.model_dump(mode="json")

    if hasattr(payment, "dict"):
        return payment.dict()

    if not isinstance(payment, dict):
        raise TypeError("Payment must be a model or dictionary")

    return payment


def _payment_fingerprint(payment: Any) -> str:
    payload = _payment_payload(payment).copy()
    payload.pop("idempotency_key", None)

    return hashlib.sha256(
        _serialize(payload).encode("utf-8")
    ).hexdigest()


async def get_idempotency_state(
    redis: Redis,
    idempotency_key: str,
) -> dict[str, Any] | None:
    try:
        raw = await redis.get(_state_key(idempotency_key))
        return _decode(raw)
    except RedisError as exc:
        logger.exception("Could not read idempotency state")
        raise IdempotencyStoreError(
            "Could not read idempotency state"
        ) from exc


async def get_idempotent_result(
    redis: Redis,
    idempotency_key: str,
) -> dict[str, Any] | None:
    state = await get_idempotency_state(
        redis,
        idempotency_key,
    )

    if state is None:
        return None

    if state.get("status") == "completed":
        result = state.get("result")

        if not isinstance(result, dict):
            raise IdempotencyStoreError(
                "Completed idempotency record has no valid result"
            )

        return result

    raise IdempotencyInProgressError(
        "Payment outcome is unresolved; reconciliation is required"
    )


async def claim_idempotency_key(
    redis: Redis,
    idempotency_key: str,
    payment: Any | None = None,
) -> bool:
    """
    Atomically claim a new request or reclaim a retryable request.

    In-progress and reconciling records are never automatically
    expired. Retryable records retain their original payment and
    attempted-provider history.
    """
    if payment is None:
        raise IdempotencyStoreError(
            "Payment data is required to claim an idempotency key"
        )

    payload = _payment_payload(payment)
    fingerprint = _payment_fingerprint(payment)

    state = {
        "status": "in_progress",
        "payment": payload,
        "payment_fingerprint": fingerprint,
        "current_provider": None,
        "attempted_providers": [],
        "last_error": None,
        "owner_token": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    key = _state_key(idempotency_key)

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        redis.call('SET', KEYS[1], ARGV[1])
        return 1
    end

    local existing = cjson.decode(raw)

    if existing.payment_fingerprint ~= ARGV[2] then
        return -1
    end

    if existing.status == 'completed' then
        return 0
    end

    if existing.status == 'retryable' then
        local replacement = cjson.decode(ARGV[1])

        replacement.attempted_providers =
            existing.attempted_providers or {}

        replacement.previous_attempts =
            existing.previous_attempts or
            existing.attempted_providers or {}

        replacement.created_at = existing.created_at

        redis.call('SET', KEYS[1], cjson.encode(replacement))
        return 1
    end

    return 0
    """

    try:
        result = await redis.eval(
            script,
            1,
            key,
            _serialize(state),
            fingerprint,
        )

        if result == -1:
            raise IdempotencyConflictError(
                "Idempotency key belongs to a different payment request"
            )

        return result == 1

    except RedisError as exc:
        logger.exception("Could not claim idempotency key")
        raise IdempotencyStoreError(
            "Could not claim idempotency key"
        ) from exc


async def record_provider_attempt(
    redis: Redis,
    idempotency_key: str,
    provider: str,
) -> dict[str, Any]:
    """Persist the selected provider before making its API call."""
    provider = provider.strip().lower()

    if not provider:
        raise ValueError("Provider cannot be empty")

    key = _state_key(idempotency_key)

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        return -1
    end

    local state = cjson.decode(raw)

    if state.status ~= 'in_progress' then
        return -2
    end

    state.current_provider = ARGV[1]

    local found = false
    state.attempted_providers =
        state.attempted_providers or {}

    for _, value in ipairs(state.attempted_providers) do
        if value == ARGV[1] then
            found = true
            break
        end
    end

    if not found then
        table.insert(state.attempted_providers, ARGV[1])
    end

    redis.call('SET', KEYS[1], cjson.encode(state))
    return cjson.encode(state)
    """

    try:
        result = await redis.eval(
            script,
            1,
            key,
            provider,
        )

        if result == -1:
            raise IdempotencyStoreError(
                "Idempotency record does not exist"
            )

        if result == -2:
            raise IdempotencyInProgressError(
                "Request is not available for another provider attempt"
            )

        return _decode(result) or {}

    except RedisError as exc:
        logger.exception("Could not record provider attempt")
        raise IdempotencyStoreError(
            "Could not record provider attempt"
        ) from exc


async def save_idempotent_result(
    redis: Redis,
    idempotency_key: str,
    result: dict[str, Any],
    reconciliation_token: str | None = None,
) -> None:
    """
    Persist the completed payment response atomically.

    Normal routing may complete an in-progress record.
    Reconciliation may complete a reconciling record only when
    it presents the currently active reconciliation token.
    """
    key = _state_key(idempotency_key)

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        return -1
    end

    local state = cjson.decode(raw)

    if state.status == 'completed' then
        return 0
    end

    if state.status == 'reconciling' then
        if not ARGV[4] or
            state.reconciliation_token ~= ARGV[4] then
            return -2
        end
    elseif state.status == 'in_progress' then
        if ARGV[4] and ARGV[4] ~= '' then
            return -2
        end
    else
        return -2
    end

    state.status = 'completed'
    state.result = cjson.decode(ARGV[1])
    state.completed_at = ARGV[2]
    state.reconciliation_token = nil
    state.reconciliation_lock_until = nil

    redis.call(
        'SET',
        KEYS[1],
        cjson.encode(state),
        'EX',
        tonumber(ARGV[3])
    )

    return 1
    """

    try:
        saved = await redis.eval(
            script,
            1,
            key,
            _serialize(result),
            datetime.now(timezone.utc).isoformat(),
            COMPLETED_TTL_SECONDS,
            reconciliation_token or "",
        )

        if saved == -1:
            raise IdempotencyStoreError(
                "Cannot complete a missing idempotency record"
            )

        if saved == -2:
            raise IdempotencyInProgressError(
                "Payment completion rejected because ownership or state changed"
            )

    except RedisError as exc:
        logger.exception("Could not save idempotent result")
        raise IdempotencyStoreError(
            "Could not save idempotent result"
        ) from exc


async def mark_idempotency_error(
    redis: Redis,
    idempotency_key: str,
    error: str,
) -> None:
    """Record diagnostic information without changing payment state."""
    key = _state_key(idempotency_key)

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        return 0
    end

    local state = cjson.decode(raw)

    if state.status ~= 'in_progress' then
        return 0
    end

    state.last_error = ARGV[1]

    redis.call('SET', KEYS[1], cjson.encode(state))
    return 1
    """

    try:
        await redis.eval(
            script,
            1,
            key,
            error[:1000],
        )
    except RedisError as exc:
        logger.exception("Could not update idempotency diagnostic state")
        raise IdempotencyStoreError(
            "Could not update idempotency diagnostic state"
        ) from exc


async def mark_idempotency_retryable(
    redis: Redis,
    idempotency_key: str,
    reason: str,
    expected_provider: str | None = None,
) -> bool:
    """
    Mark a payment retryable only after a definitive no-charge outcome.

    If expected_provider is supplied, the current provider must match.
    If omitted, no provider may currently be in progress.

    Provider history and the original payment are retained.
    """
    key = _state_key(idempotency_key)

    normalized_provider = (
        expected_provider.strip().lower()
        if expected_provider
        else ""
    )

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        return 0
    end

    local state = cjson.decode(raw)

    if state.status ~= 'in_progress' then
        return 0
    end

    if ARGV[2] == '' then
        if state.current_provider ~= nil then
            return 0
        end
    else
        if state.current_provider ~= ARGV[2] then
            return 0
        end
    end

    state.status = 'retryable'
    state.current_provider = nil
    state.last_error = ARGV[1]
    state.reconciliation_token = nil
    state.reconciliation_lock_until = nil

    redis.call('SET', KEYS[1], cjson.encode(state))
    return 1
    """

    try:
        result = await redis.eval(
            script,
            1,
            key,
            reason[:1000],
            normalized_provider,
        )
        return result == 1

    except RedisError as exc:
        logger.exception("Could not mark payment retryable")
        raise IdempotencyStoreError(
            "Could not mark payment retryable"
        ) from exc


async def acquire_reconciliation_lock(
    redis: Redis,
    idempotency_key: str,
) -> str | None:
    """
    Acquire a temporary reconciliation lease.

    A crashed reconciliation can be retried after the lease expires.
    The unresolved payment record itself remains persistent.
    """
    token = str(uuid4())
    key = _state_key(idempotency_key)

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        return -1
    end

    local state = cjson.decode(raw)

    if state.status == 'completed' then
        return -2
    end

    if state.status ~= 'in_progress'
        and state.status ~= 'reconciling' then
        return -3
    end

    local now = tonumber(ARGV[2])
    local lock_until =
        tonumber(state.reconciliation_lock_until or 0)

    if state.status == 'reconciling'
        and lock_until > now then
        return 0
    end

    state.status = 'reconciling'
    state.reconciliation_token = ARGV[1]
    state.reconciliation_lock_until =
        now + tonumber(ARGV[3])

    redis.call('SET', KEYS[1], cjson.encode(state))
    return 1
    """

    try:
        result = await redis.eval(
            script,
            1,
            key,
            token,
            datetime.now(timezone.utc).timestamp(),
            RECONCILIATION_LOCK_SECONDS,
        )

        if result == 1:
            return token

        if result == -1:
            raise IdempotencyStoreError(
                "Idempotency record does not exist"
            )

        if result == -2:
            raise IdempotencyInProgressError(
                "Payment has already been completed"
            )

        if result == -3:
            raise IdempotencyInProgressError(
                "Payment is not currently eligible for reconciliation"
            )

        return None

    except RedisError as exc:
        logger.exception("Could not acquire reconciliation lock")
        raise IdempotencyStoreError(
            "Could not acquire reconciliation lock"
        ) from exc


async def release_after_confirmed_not_found(
    redis: Redis,
    idempotency_key: str,
    provider: str,
    reconciliation_token: str | None = None,
) -> bool:
    """
    Move the request to retryable after definitive provider not-found.

    The payment request and provider history are retained.
    """
    if not reconciliation_token:
        raise IdempotencyStoreError(
            "Reconciliation token is required"
        )

    key = _state_key(idempotency_key)

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        return 0
    end

    local state = cjson.decode(raw)

    if state.status ~= 'reconciling' then
        return 0
    end

    if state.current_provider ~= ARGV[1] then
        return 0
    end

    if state.reconciliation_token ~= ARGV[2] then
        return 0
    end

    state.status = 'retryable'
    state.current_provider = nil
    state.last_error =
        'Provider definitively confirmed order not found'
    state.reconciliation_token = nil
    state.reconciliation_lock_until = nil

    redis.call('SET', KEYS[1], cjson.encode(state))
    return 1
    """

    try:
        result = await redis.eval(
            script,
            1,
            key,
            provider.strip().lower(),
            reconciliation_token,
        )
        return result == 1

    except RedisError as exc:
        logger.exception("Could not mark payment retryable")
        raise IdempotencyStoreError(
            "Could not mark payment retryable"
        ) from exc


async def release_reconciliation_lock(
    redis: Redis,
    idempotency_key: str,
    token: str,
) -> bool:
    """Return a failed or ambiguous reconciliation to unresolved state."""
    key = _state_key(idempotency_key)

    script = """
    local raw = redis.call('GET', KEYS[1])

    if not raw then
        return 0
    end

    local state = cjson.decode(raw)

    if state.status ~= 'reconciling'
        or state.reconciliation_token ~= ARGV[1] then
        return 0
    end

    state.status = 'in_progress'
    state.reconciliation_token = nil
    state.reconciliation_lock_until = nil

    redis.call('SET', KEYS[1], cjson.encode(state))
    return 1
    """

    try:
        return await redis.eval(
            script,
            1,
            key,
            token,
        ) == 1

    except RedisError as exc:
        logger.exception("Could not release reconciliation lock")
        raise IdempotencyStoreError(
            "Could not release reconciliation lock"
        ) from exc
