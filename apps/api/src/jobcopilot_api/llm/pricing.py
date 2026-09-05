"""LLM price table + cost computation (ADR-0004 D5).

DashScope OpenAI-compat does not return cost; we compute it locally from
`response.usage` plus this table. Token prices are CNY per 1M tokens;
Responses API tool prices are CNY per 1K calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Pricing:
    """Per-model pricing (CNY per 1M tokens)."""

    in_: Decimal
    cached_in: Decimal
    cache_creation_in: Decimal
    out: Decimal


@dataclass(frozen=True)
class BatchPricing:
    """Per-model batch pricing (CNY per 1M tokens)."""

    file_in: Decimal
    file_out: Decimal
    chat_in: Decimal
    chat_out: Decimal


@dataclass(frozen=True)
class ToolCallPricing:
    """Responses API tool call pricing (CNY per 1K calls)."""

    per_1k_calls: Decimal
    limited_free: bool = False


_TABLE: dict[str, Pricing] = {
    # DashScope Model Studio console, China Mainland, 2026-09-05.
    "qwen3.8-flash": Pricing(
        in_=Decimal("0.8"),
        cached_in=Decimal("0.1"),
        cache_creation_in=Decimal("1.25"),
        out=Decimal("2.7"),
    ),
    # 保留:历史 llm_calls 行仍按旧模型计价,删掉会让成本回溯失效。
    # DashScope Model Studio console, China Mainland, 2026-05-13.
    "qwen3.6-flash": Pricing(
        in_=Decimal("1.2"),
        cached_in=Decimal("0.12"),
        cache_creation_in=Decimal("1.5"),
        out=Decimal("7.2"),
    ),
    # qwen3.6-plus 已不再使用(PREMIUM 亦切至 qwen3.8-flash),不再回填。
}


_BATCH_TABLE: dict[str, BatchPricing] = {
    # DashScope Model Studio console, China Mainland, 2026-09-05.
    # chat_in / chat_out 为限时 5 折价(原价 0.8 / 2.7),折扣到期需回填。
    "qwen3.8-flash": BatchPricing(
        file_in=Decimal("0.4"),
        file_out=Decimal("1.35"),
        chat_in=Decimal("0.4"),
        chat_out=Decimal("1.35"),
    ),
    # DashScope Model Studio console, China Mainland, 2026-05-13.
    "qwen3.6-flash": BatchPricing(
        file_in=Decimal("0.6"),
        file_out=Decimal("3.6"),
        chat_in=Decimal("1.2"),
        chat_out=Decimal("7.2"),
    ),
}


_RESPONSES_TOOL_TABLE: dict[str, ToolCallPricing] = {
    # DashScope Model Studio console, China Mainland, 2026-05-13.
    "web_search": ToolCallPricing(per_1k_calls=Decimal("4")),
    "code_interpreter": ToolCallPricing(
        per_1k_calls=Decimal("0"), limited_free=True
    ),
    "web_extractor": ToolCallPricing(
        per_1k_calls=Decimal("0"), limited_free=True
    ),
    "i2i_search": ToolCallPricing(per_1k_calls=Decimal("48")),
    "t2i_search": ToolCallPricing(per_1k_calls=Decimal("24")),
}


def price_table() -> dict[str, Pricing]:
    """Read-only view of the price table (returns a shallow copy)."""
    return dict(_TABLE)


def batch_price_table() -> dict[str, BatchPricing]:
    """Read-only view of the batch price table (returns a shallow copy)."""
    return dict(_BATCH_TABLE)


def responses_tool_price_table() -> dict[str, ToolCallPricing]:
    """Read-only view of Responses API tool prices (returns a shallow copy)."""
    return dict(_RESPONSES_TOOL_TABLE)


_PER_MILLION = Decimal("1000000")


def cost_for(
    *,
    model: str,
    tokens_in: int,
    cached_tokens: int,
    tokens_out: int,
    cache_creation_input_tokens: int = 0,
) -> Decimal:
    """Compute total cost in CNY.

    Uncached input tokens are billed at `in_`, cached hits at `cached_in`,
    explicit cache writes at `cache_creation_in`, output always at `out`.
    Returns Decimal(0) for unknown models — the caller still gets a valid
    LLMResult; the missing-model condition is recorded by surrounding logging
    instead of raising here.
    """
    pricing = _TABLE.get(model)
    if pricing is None:
        return Decimal("0")

    creation_in = max(cache_creation_input_tokens, 0)
    uncached_in = max(tokens_in - cached_tokens - creation_in, 0)
    return (
        Decimal(uncached_in) * pricing.in_
        + Decimal(cached_tokens) * pricing.cached_in
        + Decimal(creation_in) * pricing.cache_creation_in
        + Decimal(tokens_out) * pricing.out
    ) / _PER_MILLION
