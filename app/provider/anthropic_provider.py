"""
Anthropic provider adapter.

Two jobs:
  1. call_non_streaming() -> returns (text, UsageResult)
  2. call_streaming()     -> async generator that yields text chunks as they
     arrive and keeps usage_holder[0] up to date WHILE streaming, so the
     caller can bill correctly even if the client disconnects halfway.

If ANTHROPIC_API_KEY is not set, MOCK MODE is used (fake replies, no network).
"""
import os
import json
import asyncio
import httpx
from app.models import UsageResult

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MOCK_MODE = ANTHROPIC_API_KEY is None
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# Wait up to 10s to connect, 60s for each read.
TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# One shared client for the whole app, so connections are reused
# instead of opening a new TCP + TLS connection on every request.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client


async def close_client() -> None:
    """Call on app shutdown to close open connections."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _headers() -> dict:
    return {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }


def _rough_token_count(text: str) -> int:
    # Rough estimate: ~4 characters per token (demo / mock only).
    return max(1, len(text) // 4)


def _message_text(content) -> str:
    """A message's content can be a plain string or a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _prompt_text(messages: list[dict]) -> str:
    return " ".join(_message_text(m.get("content", "")) for m in messages)


def _new_usage(model: str, input_tokens: int = 0, output_tokens: int = 0) -> UsageResult:
    return UsageResult(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        provider="anthropic",
    )


async def _raise_with_body(resp: httpx.Response) -> None:
    """Like raise_for_status(), but includes Anthropic's error message."""
    if resp.is_success:
        return
    body = (await resp.aread()).decode(errors="replace")[:500]
    raise httpx.HTTPStatusError(
        f"Anthropic API returned {resp.status_code}: {body}",
        request=resp.request,
        response=resp,
    )


async def call_non_streaming(model: str, messages: list[dict], max_tokens: int) -> tuple[str, UsageResult]:
    if MOCK_MODE:
        await asyncio.sleep(0.3)  # pretend network delay
        fake_output = f"[MOCK] This is a simulated reply from model {model}."
        usage = _new_usage(
            model,
            input_tokens=_rough_token_count(_prompt_text(messages)),
            output_tokens=_rough_token_count(fake_output),
        )
        return fake_output, usage

    resp = await _get_client().post(
        API_URL,
        headers=_headers(),
        json={"model": model, "max_tokens": max_tokens, "messages": messages},
    )
    await _raise_with_body(resp)
    data = resp.json()

    text = "".join(
        block.get("text", "") for block in data.get("content", [])
        if block.get("type") == "text"
    )
    usage_data = data.get("usage", {})
    usage = _new_usage(
        model,
        input_tokens=usage_data.get("input_tokens", 0),
        output_tokens=usage_data.get("output_tokens", 0),
    )
    return text, usage


async def call_streaming(model: str, messages: list[dict], max_tokens: int, usage_holder: list):
    """
    Async generator: yields each text chunk right away.

    usage_holder is a 1-item list ([None]) created by the CALLER for each
    request (never a global, since many streams run at once).

    usage_holder[0] is filled in as soon as input tokens are known and is
    updated after every chunk. So if the client disconnects halfway, the
    caller still sees the tokens generated so far and can bill for them
    (the provider charges for them too). When the stream ends normally,
    it holds the provider's final numbers.
    """
    if MOCK_MODE:
        usage = _new_usage(model, input_tokens=_rough_token_count(_prompt_text(messages)))
        usage_holder[0] = usage
        fake_chunks = ["[MOCK] ", "This ", "is ", "a ", "simulated ", "streaming ", "reply."]
        output_acc = ""
        for chunk in fake_chunks:
            await asyncio.sleep(0.1)
            output_acc += chunk
            usage.output_tokens = _rough_token_count(output_acc)
            yield chunk
        return

    async with _get_client().stream(
        "POST", API_URL,
        headers=_headers(),
        json={"model": model, "max_tokens": max_tokens, "messages": messages, "stream": True},
    ) as resp:
        await _raise_with_body(resp)

        usage = _new_usage(model)
        usage_holder[0] = usage
        output_text = ""          # text received so far, for an estimate
        got_final_count = False   # True once Anthropic sends an output count

        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line.removeprefix("data:").strip()
            if not payload:
                continue
            event = json.loads(payload)
            etype = event.get("type")

            if etype == "message_start":
                # Anthropic sends the input token count at the very start.
                msg_usage = event.get("message", {}).get("usage", {})
                usage.input_tokens = msg_usage.get("input_tokens", 0)
                usage.output_tokens = msg_usage.get("output_tokens", 0)

            elif etype == "content_block_delta":
                delta_text = event.get("delta", {}).get("text", "")
                if delta_text:
                    output_text += delta_text
                    if not got_final_count:
                        # Keep a running estimate until the real count arrives.
                        usage.output_tokens = max(usage.output_tokens, _rough_token_count(output_text))
                    yield delta_text

            elif etype == "message_delta":
                # Real output count from Anthropic (cumulative): it replaces the estimate.
                out = event.get("usage", {}).get("output_tokens")
                if out is not None:
                    usage.output_tokens = out
                    got_final_count = True

            elif etype == "error":
                # Anthropic can send an error mid-stream (e.g. "overloaded_error").
                err = event.get("error", {})
                raise RuntimeError(
                    f"Anthropic stream error: {err.get('type', 'unknown')}: {err.get('message', '')}"
                )
