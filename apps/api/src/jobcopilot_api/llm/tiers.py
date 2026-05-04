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


_TIER_TABLE: dict[Tier, TierConfig] = {
    # ADR-0003 §22 + ADR-0004 D1 / D3.
    # default_max_tokens: 简历解析单次输出可达 4-6K tokens(JSON + 多段经历);
    # DashScope 默认 4096 不够,8192 给余量。PREMIUM 给 16384 留给定制简历 / 长文。
    Tier.CHEAP: TierConfig(
        model="qwen3.6-flash",
        thinking_mode=False,
        default_timeout_s=30.0,
        default_max_tokens=8192,
    ),
    Tier.STANDARD: TierConfig(
        model="qwen3.6-flash",
        thinking_mode=True,
        default_timeout_s=30.0,
        default_max_tokens=8192,
    ),
    Tier.PREMIUM: TierConfig(
        model="qwen3.6-plus",
        thinking_mode=True,
        default_timeout_s=60.0,
        default_max_tokens=16384,
    ),
}


def tier_to_model(tier: Tier) -> TierConfig:
    """Resolve a tier into its concrete config."""
    return _TIER_TABLE[tier]
