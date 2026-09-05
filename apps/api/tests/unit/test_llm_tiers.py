"""Unit tests for jobcopilot_api.llm.tiers."""

from __future__ import annotations

import pytest

from jobcopilot_api.llm.tiers import Tier, tier_to_model


def test_cheap_tier_uses_flash_with_thinking() -> None:
    cfg = tier_to_model(Tier.CHEAP)
    assert cfg.model == "qwen3.8-flash"
    assert cfg.thinking_mode is True
    assert cfg.reasoning_effort == "medium"
    assert cfg.default_timeout_s == 30.0


def test_standard_tier_uses_flash_with_thinking() -> None:
    cfg = tier_to_model(Tier.STANDARD)
    assert cfg.model == "qwen3.8-flash"
    assert cfg.thinking_mode is True


def test_premium_tier_uses_flash_with_thinking_and_higher_timeout() -> None:
    cfg = tier_to_model(Tier.PREMIUM)
    assert cfg.model == "qwen3.8-flash"
    assert cfg.thinking_mode is True
    assert cfg.default_timeout_s == 60.0


def test_tier_str_value_matches_db_column() -> None:
    # llm_calls.tier is VARCHAR(20); the str value is what gets persisted.
    assert Tier.CHEAP.value == "cheap"
    assert Tier.STANDARD.value == "standard"
    assert Tier.PREMIUM.value == "premium"


def test_unknown_tier_raises() -> None:
    with pytest.raises(KeyError):
        tier_to_model("nonexistent")  # type: ignore[arg-type]
