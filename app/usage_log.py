"""
STEP 4: USAGE LOGGING (append-only)

This is the source of truth. Old records are never edited or deleted.
The demo uses an in-memory list. In practice: a usage_logs table in Postgres,
insert-only, indexed on (user_id, created_at) so reports query fast.
"""
import uuid
import threading
from datetime import datetime, timezone
from app.models import UsageLogEntry

_logs: list[UsageLogEntry] = []
_lock = threading.Lock()


def append_log(entry: UsageLogEntry):
    with _lock:
        _logs.append(entry)


def get_logs_for_user(user_id: str) -> list[UsageLogEntry]:
    with _lock:
        return [l for l in _logs if l.user_id == user_id]


def new_log_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
