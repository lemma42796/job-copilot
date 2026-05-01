"""LLM price table + cost computation (ADR-0004 D5).

DashScope OpenAI-compat does not return cost; we compute it locally from
`response.usage` plus this table. Prices are CNY per 1M tokens, sourced
from ADR-0003 (Qwen3.6) on 2026-05-01.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Pricing:
    """Per-model pricing (CNY per 1M tokens)."""

    in_: Decimal
    cached_in: Decimal
    out: Decimal


_TABLE: dict[str, Pricing] = {
    # ADR-0003 §「负面」表格(2026-05-01)
    "qwen3.6-flash": Pricing(
        in_=Decimal("0.6"),
        cached_in=Decimal("0.12"),
        out=Decimal("7.2"),
    ),
    # qwen3.6-plus: 实测后回填 (ADR-0003 §「负面」第 4 条)
}


def price_table() -> dict[str, Pricing]:
    """Read-only view of the price table (returns a shallow copy)."""
    return dict(_TABLE)


_PER_MILLION = Decimal("1000000")


def cost_for(
    *,
    model: str,
    tokens_in: int,
    cached_tokens: int,
    tokens_out: int,
) -> Decimal:
    """Compute total cost in CNY.

    Uncached input tokens are billed at `in_`, cached portion at `cached_in`,
    output always at `out`. Returns Decimal(0) for unknown models — the
    caller still gets a valid LLMResult; the missing-model condition is
    recorded by surrounding logging instead of raising here.
    """
    pricing = _TABLE.get(model)
    if pricing is None:
        return Decimal("0")

    uncached_in = max(tokens_in - cached_tokens, 0)
    return (
        Decimal(uncached_in) * pricing.in_
        + Decimal(cached_tokens) * pricing.cached_in
        + Decimal(tokens_out) * pricing.out
    ) / _PER_MILLION
