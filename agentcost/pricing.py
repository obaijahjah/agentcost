"""Pricing table — per-million-token USD rates.

These are public list prices as of 2026-10. For a real production tool, fetch
live prices from each provider's pricing API and cache per-call. The prototype
hard-codes them so the cost math is reproducible and the demo does not depend
on a pricing endpoint that can change.
"""

# (input_usd_per_1m, output_usd_per_1m)
PRICES = {
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4_5": (3.00, 15.00),
}

# Synthetic "what-if" models used in the failure demo so we can show
# cost math without depending on a real provider every time.
SYNTHETIC = {
    "synthetic-cheap": (0.01, 0.04),
    "synthetic-expensive": (50.00, 200.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return cost in USD for a single call. Unknown models raise."""
    table = {**PRICES, **SYNTHETIC}
    if model not in table:
        raise KeyError(
            f"Unknown model {model!r}. Known: {sorted(table)}"
        )
    in_rate, out_rate = table[model]
    return (input_tokens / 1_000_000) * in_rate + (
        output_tokens / 1_000_000
    ) * out_rate
