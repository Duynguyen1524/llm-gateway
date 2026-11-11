# LLM Gateway Demo (FastAPI)

Demo gateway illustrating 4 foundational concepts: **Auth → Rate limit → Quota → Usage logging**, applied to both regular and streaming requests.

## Running it

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

No real API key needed — if the `ANTHROPIC_API_KEY` environment variable isn't set, the gateway automatically runs in **MOCK MODE** (simulated responses, no outbound network calls) so you can demo the entire logic flow without spending money or needing a key.

To call the real Anthropic API:

```bash
export ANTHROPIC_API_KEY=sk-ant-xxxx
uvicorn app.main:app --reload --port 8000
```

## 2 virtual keys available for testing (see `app/auth.py`)

| key | user | allowed models | monthly budget | RPM | TPM |
|---|---|---|---|---|---|
| `sk-gw-alice-001` | alice | sonnet, haiku | $5.00 | 5 | 20,000 |
| `sk-gw-bob-002` | bob | haiku only | $1.00 | 3 | 5,000 |

## Quick tests

```bash
# Regular request
curl -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer sk-gw-alice-001" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","messages":[{"role":"user","content":"hello"}]}'

# Streaming
curl -N -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer sk-gw-alice-001" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","messages":[{"role":"user","content":"hello"}],"stream":true}'

# View usage log
curl http://localhost:8000/v1/usage/alice
```

## Code → theory mapping

| File | Concept |
|---|---|
| `app/auth.py` | **Auth**: virtual key → user, allowed models |
| `app/rate_limit.py` | **Rate limit**: token bucket, RPM and TPM tracked separately |
| `app/quota.py` | **Quota**: monthly USD budget, pre-check before + commit after |
| `app/pricing.py` | USD/1K token pricing table, computes actual cost from usage |
| `app/usage_log.py` | Immutable (append-only) log — source of truth for billing/audit |
| `app/providers/anthropic_provider.py` | Provider adapter, handles both non-streaming and streaming |
| `app/main.py` | Wires together the full 10-step flow: auth → rate limit → quota → forward → log → commit |

## Limitations of this demo (needed for production)

This is a minimal demo for learning the fundamentals — there are 3 **intentional simplifications** that need to be replaced for a real deployment:

1. **In-memory state** (`dict`, `list`) instead of Redis + Postgres
   → all data is lost on restart, and it can't run across multiple instances.
   → replace `RateLimiter`/`QuotaManager` with a Redis-backed version (atomic via Lua scripts), and `usage_log.py` with an actual Postgres table.

2. **No proper transaction/locking for the quota race condition**
   → concurrent requests can "slip through" the pre-check before the commit updates in time.
   → production needs a "soft reservation" mechanism (reserve budget upfront, refund on failure).

3. **Pre-call token estimation (`_rough_token_count`) is very crude** (4 characters/token)
   → production should use each model's real tokenizer (e.g. `tiktoken` for OpenAI) to estimate TPM more accurately.
