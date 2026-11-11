"""
STEP 2: RATE LIMIT
Algorithm: Token Bucket — allows controlled bursts while still capping the average rate.

The demo uses an in-memory dict (NOT safe across multiple instances/processes).
In a real system, replace it with Redis + a Lua script to make it atomic:

    -- Lua script running on Redis: "read + compute + write" happens as one operation,
    -- so concurrent requests can't race each other.
    local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'last_refill')
    ...

Each user gets 2 separate buckets:
  - RPM bucket: each "token" in the bucket = 1 request
  - TPM bucket: each "token" in the bucket = 1 LLM token
"""
import time
import threading


class TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now

    def try_consume(self, amount: float) -> bool:
        """Returns True if consumed successfully, False if the bucket doesn't have enough."""
        with self.lock:
            self._refill()
            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False

    def available(self) -> float:
        with self.lock:
            self._refill()
            return self.tokens


class RateLimiter:
    """
    Manages 2 kinds of bucket (RPM, TPM) per user_id.
    Buckets are created lazily the first time a user calls.
    """
    def __init__(self):
        self._rpm_buckets: dict[str, TokenBucket] = {}
        self._tpm_buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_rpm_bucket(self, user_id: str, rpm_limit: int) -> TokenBucket:
        with self._lock:
            if user_id not in self._rpm_buckets:
                # refill_per_second = rpm_limit / 60 -> refills evenly over one minute
                self._rpm_buckets[user_id] = TokenBucket(capacity=rpm_limit, refill_per_second=rpm_limit / 60)
            return self._rpm_buckets[user_id]

    def _get_tpm_bucket(self, user_id: str, tpm_limit: int) -> TokenBucket:
        with self._lock:
            if user_id not in self._tpm_buckets:
                self._tpm_buckets[user_id] = TokenBucket(capacity=tpm_limit, refill_per_second=tpm_limit / 60)
            return self._tpm_buckets[user_id]

    def check_request_allowed(self, user_id: str, rpm_limit: int) -> bool:
        """Called BEFORE forwarding the request — costs just 1 'request token'."""
        bucket = self._get_rpm_bucket(user_id, rpm_limit)
        return bucket.try_consume(1)

    def check_and_reserve_tokens(self, user_id: str, tpm_limit: int, estimated_tokens: int) -> bool:
        """
        Called BEFORE forwarding — a rough estimate of the tokens this request will use
        (e.g. count input tokens with a tokenizer, plus the max expected output).
        """
        bucket = self._get_tpm_bucket(user_id, tpm_limit)
        return bucket.try_consume(estimated_tokens)


# one shared instance for the whole app (in practice: a shared Redis connection)
rate_limiter = RateLimiter()
