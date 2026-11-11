"""
Main gateway — wires together the full flow:

1. AUTH            -> app/auth.py           (authenticate dependency)
2. RATE LIMIT      -> app/rate_limit.py     (token bucket, RPM + TPM)
3. QUOTA PRE-CHECK -> app/quota.py          (is there any budget left?)
4. FORWARD         -> app/providers/anthropic_provider.py
5. REAL COST       -> app/pricing.py        (based on real usage from the provider)
6. LOG             -> app/usage_log.py      (append-only)
7. UPDATE QUOTA    -> app/quota.py          (commit_usage)

Run it:
    uvicorn app.main:app --reload --port 8000

No ANTHROPIC_API_KEY needed for the demo — MOCK MODE turns on automatically if it's missing.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import authenticate
from app.models import VirtualKey, UsageLogEntry
from app.rate_limit import rate_limiter
from app.quota import quota_manager
from app.pricing import calculate_cost, get_price
from app.usage_log import append_log, new_log_id, now_iso, get_logs_for_user
from app.providers import anthropic_provider as anthropic


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # On shutdown: close the shared HTTP connections to the provider.
    await anthropic.close_client()


app = FastAPI(title="LLM Gateway Demo", lifespan=lifespan)


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    max_tokens: int = 512
    stream: bool = False


@app.post("/v1/chat")
async def chat(req: ChatRequest, vkey: VirtualKey = Depends(authenticate)):
    """
    Main endpoint. `Depends(authenticate)` = STEP 1, runs automatically before
    this function — if the key is wrong, FastAPI returns 401 and we never get here.
    """

    # ---- Is this virtual key allowed to use this model? ----
    if req.model not in vkey.allowed_models:
        raise HTTPException(
            status_code=403,
            detail=f"Virtual key '{vkey.key}' is not allowed to use model '{req.model}'",
        )

    # ---- STEP 2: RATE LIMIT ----
    if not rate_limiter.check_request_allowed(vkey.user_id, vkey.rpm_limit):
        raise HTTPException(status_code=429, detail="Requests per minute (RPM) limit exceeded. Try again later.")

    estimated_tokens = sum(len(str(m.get("content", ""))) // 4 for m in req.messages) + req.max_tokens
    if not rate_limiter.check_and_reserve_tokens(vkey.user_id, vkey.tpm_limit, estimated_tokens):
        raise HTTPException(status_code=429, detail="Tokens per minute (TPM) limit exceeded. Try again later.")

    # ---- STEP 3: QUOTA PRE-CHECK ----
    if not quota_manager.pre_check(vkey.user_id, vkey.budget_limit_usd):
        raise HTTPException(status_code=402, detail="Monthly budget used up.")

    # ---- STEPS 4-7: FORWARD + COST + LOG + UPDATE QUOTA ----
    if req.stream:
        return await _handle_streaming(req, vkey)
    else:
        return await _handle_non_streaming(req, vkey)


async def _handle_non_streaming(req: ChatRequest, vkey: VirtualKey):
    try:
        text, usage = await anthropic.call_non_streaming(req.model, req.messages, req.max_tokens)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Provider call failed: {e}")

    cost = _finalize_usage(vkey, usage)

    return {
        "model": req.model,
        "content": text,
        "usage": usage.model_dump(),
        "cost_usd": cost,
        "budget_used_this_month": quota_manager.get_usage(vkey.user_id),
    }


async def _handle_streaming(req: ChatRequest, vkey: VirtualKey):
    usage_holder = [None]

    async def event_generator():
        try:
            async for chunk in anthropic.call_streaming(req.model, req.messages, req.max_tokens, usage_holder):
                # forward each chunk to the client RIGHT AWAY -> the first branch of the "tee"
                yield chunk
        finally:
            # finally guarantees: even if the client disconnects midway, the log and quota
            # are still updated with the tokens generated up to that point.
            if usage_holder[0] is not None:
                _finalize_usage(vkey, usage_holder[0])
            else:
                # disconnected before any usage arrived -> log with usage = 0,
                # marked status="cancelled" so nobody is charged unfairly
                _log_only(vkey, req.model, 0, 0, status="cancelled")

    return StreamingResponse(event_generator(), media_type="text/plain")


def _finalize_usage(vkey: VirtualKey, usage) -> float:
    """STEPS 5-7 combined: compute real cost -> write log -> add to quota."""
    cost = calculate_cost(usage.provider, usage.model, usage.input_tokens, usage.output_tokens)
    price = get_price(usage.provider, usage.model)

    append_log(UsageLogEntry(
        id=new_log_id(),
        user_id=vkey.user_id,
        key=vkey.key,
        provider=usage.provider,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        input_price_per_1k=price["input"],
        output_price_per_1k=price["output"],
        cost_usd=cost,
        created_at=now_iso(),
        status="success",
    ))
    quota_manager.commit_usage(vkey.user_id, cost)
    return cost


def _log_only(vkey: VirtualKey, model: str, input_tokens: int, output_tokens: int, status: str):
    append_log(UsageLogEntry(
        id=new_log_id(),
        user_id=vkey.user_id,
        key=vkey.key,
        provider="anthropic",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price_per_1k=0,
        output_price_per_1k=0,
        cost_usd=0,
        created_at=now_iso(),
        status=status,
    ))


@app.get("/v1/usage/{user_id}")
async def get_usage(user_id: str):
    """Extra endpoint to view logs + total cost — useful for demos and audits."""
    logs = get_logs_for_user(user_id)
    return {
        "user_id": user_id,
        "total_cost_this_month": quota_manager.get_usage(user_id),
        "logs": [l.model_dump() for l in logs],
    }
