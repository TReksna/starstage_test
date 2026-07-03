"""OpenAI client wrapper and per-call cost accounting."""
from settings import settings

# Approximate OpenAI pricing in USD per 1,000,000 tokens. Longest prefix wins.
MODEL_PRICING = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4.1-mini": {"in": 0.40, "out": 1.60},
    "gpt-4.1": {"in": 2.00, "out": 8.00},
    "o4-mini": {"in": 1.10, "out": 4.40},
}
DEFAULT_PRICING = {"in": 1.00, "out": 3.00}


def cost_for(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimated USD cost for one call, by longest matching model prefix."""
    price = DEFAULT_PRICING
    best_len = -1
    for prefix, p in MODEL_PRICING.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            price, best_len = p, len(prefix)
    return (tokens_in / 1_000_000) * price["in"] + (tokens_out / 1_000_000) * price["out"]


def render_messages(messages: list) -> str:
    """Flatten a chat-messages list into readable text for the run log."""
    return "\n\n".join(f"[{m.get('role', '?').upper()}]\n{m.get('content', '')}" for m in messages)


async def llm_call(
    messages: list, model: str, max_tokens: int = 500, temperature: float = 0.1
) -> tuple[str, int, int]:
    """Call OpenAI chat completion. Returns (content, tokens_in, tokens_out)."""
    import openai

    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
    tokens_out = getattr(usage, "completion_tokens", 0) or 0
    return content, tokens_in, tokens_out
