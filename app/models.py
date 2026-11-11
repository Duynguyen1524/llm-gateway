"""
Shared data schemas used across the gateway.
"""
from pydantic import BaseModel
from typing import Literal


class VirtualKey(BaseModel):
    """
    One virtual key -> mapped to a real user.
    In practice this is a Postgres table; this demo uses an in-memory dict.
    """
    key: str
    user_id: str
    allowed_models: list[str]
    budget_limit_usd: float          # money limit per month
    rpm_limit: int                   # requests per minute
    tpm_limit: int                   # tokens per minute
    active: bool = True


class UsageResult(BaseModel):
    """
    Real usage taken from the provider's response (OpenAI/Anthropic).
    """
    input_tokens: int
    output_tokens: int
    model: str
    provider: Literal["openai", "anthropic"]


class UsageLogEntry(BaseModel):
    """
    Immutable (append-only) log record — the source of truth for billing/audit.
    """
    id: str
    user_id: str
    key: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    input_price_per_1k: float
    output_price_per_1k: float
    cost_usd: float
    created_at: str
    status: Literal["success", "error", "cancelled"]
