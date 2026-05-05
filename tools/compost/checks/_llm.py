from __future__ import annotations

import json

_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-haiku-4-5-20251001": (0.8, 4.0),
}


def call_llm_anthropic(model: str, system: str, messages: list[dict]) -> tuple[str, dict]:
    """Call Anthropic messages API. Returns (text, usage_dict)."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed. Run: pip install 'anthropic>=0.40'")
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=messages,
    )
    text = response.content[0].text if response.content else ""
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    }
    return text, usage


def parse_json_field(text: str, field: str) -> list[dict]:
    """Parse a JSON response and return the list at `field`. Tolerates code fences."""
    try:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            end = -1 if lines[-1].strip() in ("```", "") else len(lines)
            text = "\n".join(lines[1:end]).strip()
        data = json.loads(text)
        return data.get(field, []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, AttributeError):
        return []


def compute_cost(model: str, usage: dict) -> float:
    in_rate, out_rate = _PRICING.get(model, (3.0, 15.0))
    input_cost = usage["input_tokens"] / 1_000_000 * in_rate
    output_cost = usage["output_tokens"] / 1_000_000 * out_rate
    cache_read = usage.get("cache_read_input_tokens", 0) / 1_000_000 * (in_rate * 0.1)
    return input_cost + output_cost + cache_read
