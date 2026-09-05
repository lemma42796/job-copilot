"""LLM tier definitions (ADR-0003 / ADR-0004 D1).

`Tier` is the abstract knob agents pass; the concrete (model, thinking_mode,
default timeout) tuple lives here so swapping providers later only touches
this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Tier(StrEnum):
    CHEAP = "cheap"
    STANDARD = "standard"
    PREMIUM = "premium"


@dataclass(frozen=True)
class TierConfig:
    model: str
    thinking_mode: bool
    default_timeout_s: float
    default_max_tokens: int
    # qwen3.8 系列的思考力度档位,仅在 thinking_mode=True 时下发。
    # 三档:low(budget 4096)/ medium(16384)/ xhigh(262144)。
    # 注意不可与 thinking_budget 同时下发,同设上游报错,故全仓不设 budget。
    reasoning_effort: str | None = None


_TIER_TABLE: dict[Tier, TierConfig] = {
    # ADR-0003 §22 + ADR-0004 D1 / D3.
    # default_max_tokens: 简历解析单次输出可达 4-6K tokens(JSON + 多段经历);
    # DashScope 默认 4096 不够,8192 给余量。PREMIUM 给 16384 留给定制简历 / 长文。
    Tier.CHEAP: TierConfig(
        model="qwen3.8-flash",
        thinking_mode=True,
        default_timeout_s=60.0,
        default_max_tokens=8192,
        reasoning_effort="medium",
    ),
    Tier.STANDARD: TierConfig(
        model="qwen3.8-flash",
        thinking_mode=True,
        default_timeout_s=60.0,
        default_max_tokens=8192,
        reasoning_effort="medium",
    ),
    # PREMIUM 与 CHEAP/STANDARD 同模型,差异仅在 thinking / timeout / max_tokens。
    # 评委与被评 agent 因此不再是不同模型,自评偏高风险见 evals/judge.py 说明。
    Tier.PREMIUM: TierConfig(
        model="qwen3.8-flash",
        thinking_mode=True,
        default_timeout_s=60.0,
        default_max_tokens=16384,
        reasoning_effort="medium",
    ),
}


def tier_to_model(tier: Tier) -> TierConfig:
    """Resolve a tier into its concrete config."""
    return _TIER_TABLE[tier]
