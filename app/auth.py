"""
STEP 1: AUTHENTICATION
Look up a virtual key -> user, allowed models, limits.

The demo uses an in-memory dict. In a real system this is a Postgres table:

    CREATE TABLE api_keys (
        key_hash        TEXT PRIMARY KEY,   -- store a hash, never the raw key
        user_id         TEXT NOT NULL,
        allowed_models  TEXT[] NOT NULL,
        budget_limit_usd NUMERIC NOT NULL,
        rpm_limit       INT NOT NULL,
        tpm_limit       INT NOT NULL,
        active          BOOLEAN DEFAULT TRUE
    );

Cache lookups in Redis (TTL of a few tens of seconds) to avoid hitting
the DB on every request — this step runs on EVERY request.
"""
from fastapi import HTTPException, Header
from app.models import VirtualKey

# --- Fake "DB" for the demo ---
_FAKE_KEY_STORE: dict[str, VirtualKey] = {
    "sk-gw-alice-001": VirtualKey(
        key="sk-gw-alice-001",
        user_id="alice",
        allowed_models=["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        budget_limit_usd=5.00,
        rpm_limit=5,
        tpm_limit=20000,
    ),
    "sk-gw-bob-002": VirtualKey(
        key="sk-gw-bob-002",
        user_id="bob",
        allowed_models=["claude-haiku-4-5-20251001"],
        budget_limit_usd=1.00,
        rpm_limit=3,
        tpm_limit=5000,
    ),
}


def get_virtual_key_store() -> dict[str, VirtualKey]:
    return _FAKE_KEY_STORE


async def authenticate(authorization: str = Header(...)) -> VirtualKey:
    """
    FastAPI dependency — runs before any other logic.
    Client sends: Authorization: Bearer sk-gw-alice-001
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    key = authorization.removeprefix("Bearer ").strip()
    vkey = _FAKE_KEY_STORE.get(key)

    if vkey is None or not vkey.active:
        raise HTTPException(status_code=401, detail="Virtual key is invalid or has been disabled")

    return vkey
