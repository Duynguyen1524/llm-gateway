"""
Price table per model. Unit: USD per 1,000 tokens.

IMPORTANT: prices change over time, and providers can update them at any moment.
In a real system this table should live in the DB (not hardcoded), be editable
through an admin API, and carry an effective_date so old logs keep the correct
historical price.
"""

PRICING = {
    # provider -> model -> {input, output} (USD / 1K tokens)
    "anthropic": {
        "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
        "claude-haiku-4-5-20251001": {"input": 0.0008, "output": 0.004},
        "claude-opus-4-8": {"input": 0.015, "output": 0.075},
    },
    "openai": {
        "gpt-4o": {"input": 0.0025, "output": 0.010},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    },
}


def get_price(provider: str, model: str) -> dict:
    try:
        return PRICING[provider][model]
    except KeyError:
        # No price found -> fail loudly, never silently charge 0
        # (a wrong price = real money lost, so a clear error beats a silent mistake)
        raise ValueError(f"No price for {provider}/{model}. PRICING needs updating.")


def calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    price = get_price(provider, model)
    cost = (input_tokens / 1000) * price["input"] + (output_tokens / 1000) * price["output"]
    return round(cost, 8)
