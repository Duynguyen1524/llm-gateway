"""
STEP 3: QUOTA (long-term limit, in USD)

Principle: "estimate first, settle later"
  - pre_check(): only asks "is there budget left?" -> cheap, runs before calling the provider
  - commit_usage(): adds the REAL cost once the response is back -> runs after

The demo uses an in-memory dict, keyed by (user_id, period).
In practice: a usage_period table in Postgres, using UPDATE ... SET used = used + X
inside a transaction to avoid race conditions when many requests arrive at once.
"""
import threading
from datetime import datetime, timezone


class QuotaManager:
    def __init__(self):
        self._usage: dict[str, float] = {}  # key: f"{user_id}:{period}" -> total USD used
        self._lock = threading.Lock()

    @staticmethod
    def _current_period() -> str:
        now = datetime.now(timezone.utc)
        return f"{now.year:04d}-{now.month:02d}"  # monthly, e.g. "2026-08"

    def pre_check(self, user_id: str, budget_limit_usd: float) -> bool:
        """True if the user still has budget left in the current period."""
        period = self._current_period()
        key = f"{user_id}:{period}"
        with self._lock:
            used = self._usage.get(key, 0.0)
        return used < budget_limit_usd

    def commit_usage(self, user_id: str, cost_usd: float) -> float:
        """Add the REAL cost after the response. Returns the total used this period."""
        period = self._current_period()
        key = f"{user_id}:{period}"
        with self._lock:
            self._usage[key] = self._usage.get(key, 0.0) + cost_usd
            return self._usage[key]

    def get_usage(self, user_id: str) -> float:
        period = self._current_period()
        key = f"{user_id}:{period}"
        with self._lock:
            return self._usage.get(key, 0.0)


quota_manager = QuotaManager()
