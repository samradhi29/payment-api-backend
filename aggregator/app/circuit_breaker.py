class RedisCircuitBreaker:
    """
    Redis-backed circuit breaker with a single half-open recovery probe.

    Normal closed state:
        Requests are allowed without acquiring a probe lock.

    Open state:
        Requests are rejected for recovery_timeout_seconds.

    Half-open state:
        Once the open TTL expires, exactly one request can acquire the
        probe lock. Other requests are rejected until that probe succeeds,
        fails, or its short lease expires.
    """
    RECORD_FAILURE_SCRIPT = """
    local failures = redis.call("INCR", KEYS[1])

    if failures == 1 then
        redis.call("EXPIRE", KEYS[1], ARGV[1])
    end

    -- A failed half-open probe immediately reopens the circuit.
    if redis.call("EXISTS", KEYS[3]) == 1 then
        redis.call("SET", KEYS[2], "1", "EX", ARGV[3])
        redis.call("DEL", KEYS[1], KEYS[4])
        return tonumber(ARGV[2])
    end

    if failures >= tonumber(ARGV[2]) then
        redis.call("SET", KEYS[2], "1", "EX", ARGV[3])
        redis.call("SET", KEYS[3], "1", "EX", ARGV[4])
        redis.call("DEL", KEYS[1], KEYS[4])
    end

    return failures
    """

    RECORD_SUCCESS_SCRIPT = """
    redis.call("DEL", KEYS[1], KEYS[2], KEYS[3], KEYS[4])
    return 1
    """

    IS_OPEN_SCRIPT = """
    if redis.call("EXISTS", KEYS[1]) == 1 then
        return 1
    end

    if redis.call("EXISTS", KEYS[2]) == 1
        and redis.call("EXISTS", KEYS[3]) == 1 then
        return 1
    end

    return 0
    """

    ACQUIRE_PROBE_SCRIPT = """
    -- A live open key always blocks requests.
    if redis.call("EXISTS", KEYS[1]) == 1 then
        return 0
    end

    -- No probe marker means the circuit is fully closed.
    if redis.call("EXISTS", KEYS[2]) == 0 then
        return 2
    end

    -- The open TTL expired. Only one caller may own the probe lease.
    local acquired = redis.call(
        "SET", KEYS[3], ARGV[1], "NX", "EX", ARGV[2]
    )

    if acquired then
        return 1
    end

    return 0
    """

    def __init__(
        self,
        redis_client,
        failure_threshold: int = 3,
        recovery_timeout_seconds: int = 30,
        failure_window_seconds: int = 60,
        probe_lease_seconds: int = 15,
        half_open_marker_ttl_seconds: int = 604800,
    ):
        for name, value in (
            ("failure_threshold", failure_threshold),
            ("recovery_timeout_seconds", recovery_timeout_seconds),
            ("failure_window_seconds", failure_window_seconds),
            ("probe_lease_seconds", probe_lease_seconds),
            ("half_open_marker_ttl_seconds", half_open_marker_ttl_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise ValueError(f"{name} must be a positive integer")

        if probe_lease_seconds > recovery_timeout_seconds:
            raise ValueError(
                "probe_lease_seconds cannot exceed recovery_timeout_seconds"
            )

        self.redis = redis_client
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.failure_window_seconds = failure_window_seconds
        self.probe_lease_seconds = probe_lease_seconds
        self.half_open_marker_ttl_seconds = half_open_marker_ttl_seconds

    @staticmethod
    def _keys(provider: str) -> tuple[str, str, str, str]:
        normalized = str(provider or "").strip().lower()

        if not normalized:
            raise ValueError("Provider cannot be empty")

        if len(normalized) > 100:
            raise ValueError("Provider name is too long")

        prefix = f"aggregator:circuit:{normalized}"

        return (
            f"{prefix}:failures",
            f"{prefix}:open",
            f"{prefix}:half_open",
            f"{prefix}:probe",
        )

    async def is_open(self, provider: str) -> bool:
        """
        Return True while the circuit is actively open or another request
        currently owns the half-open probe.

        After the recovery TTL expires, this returns False when no probe is
        currently running, allowing the candidate to reach probe acquisition.
        """
        _, open_key, half_open_key, probe_key = self._keys(provider)

        result = await self.redis.eval(
            self.IS_OPEN_SCRIPT,
            3,
            open_key,
            half_open_key,
            probe_key,
        )

        return bool(result)

    async def try_acquire_probe(self, provider: str) -> bool:
        """
        Atomically authorize a gateway attempt.

        Returns True for a normal closed circuit or for the one request that
        acquires the half-open probe lease. Returns False when open or when
        another request already owns the probe.
        """
        _, open_key, half_open_key, probe_key = self._keys(provider)

        result = await self.redis.eval(
            self.ACQUIRE_PROBE_SCRIPT,
            3,
            open_key,
            half_open_key,
            probe_key,
            "probe",
            self.probe_lease_seconds,
        )

        return int(result) in (1, 2)

    async def record_success(self, provider: str) -> None:
        failure_key, open_key, half_open_key, probe_key = self._keys(provider)

        await self.redis.eval(
            self.RECORD_SUCCESS_SCRIPT,
            4,
            failure_key,
            open_key,
            half_open_key,
            probe_key,
        )

    async def record_failure(self, provider: str) -> int:
        failure_key, open_key, half_open_key, probe_key = self._keys(provider)

        failures = await self.redis.eval(
            self.RECORD_FAILURE_SCRIPT,
            4,
            failure_key,
            open_key,
            half_open_key,
            probe_key,
            self.failure_window_seconds,
            self.failure_threshold,
            self.recovery_timeout_seconds,
            self.half_open_marker_ttl_seconds,
        )

        return int(failures)
